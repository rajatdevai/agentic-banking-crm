"""
OutreachGenAgent — LLM call #2 (gpt-4o).

Reads: customer_profile, opportunities, explanation, recommended_products from state
Writes: outreach_messages (list[OutreachMessage])

Flow:
    1. Retrieve persona tone guidelines from RAG (persona_playbooks collection)
    2. For each channel (WhatsApp default + email), construct masked Jinja2 prompt
    3. Call gpt-4o, validate output
    4. Scan generated message for PII vault tokens — replace with placeholders if found
    5. Set pii_safe=True only after passing the vault scan
"""

from __future__ import annotations

import re
from typing import Optional

import structlog

from services.orchestrator.agents.base_agent import BaseAgent
from services.orchestrator.graph.state import AgentState
from services.orchestrator.llm.prompt_registry import PromptKey, render_prompt
from services.orchestrator.llm.router import get_llm_router
from shared.constants.enums import OutreachChannel
from shared.models.agent_state import (
    CustomerProfile,
    DetectedEvent,
    Opportunity,
    OutreachMessage,
    ProductRecommendation,
)

logger = structlog.get_logger(__name__)

# PII vault token pattern — tokens injected by PIIMasker look like <PII_XXXXX>
_VAULT_TOKEN_PATTERN = re.compile(r"<PII_[A-Z0-9_]+>")

# Default channels to generate — can be extended per opportunity
_DEFAULT_CHANNELS = [OutreachChannel.WHATSAPP]


class OutreachGenAgent(BaseAgent):
    agent_name = "OutreachGenAgent"
    timeout_seconds = 60.0   # Multiple LLM calls per execution

    async def execute(self, state: AgentState) -> dict:
        cp: Optional[CustomerProfile] = state.get("customer_profile")
        opportunities: list[Opportunity] = state.get("opportunities") or []
        explanation: Optional[str] = state.get("explanation")
        products: list[ProductRecommendation] = state.get("recommended_products") or []

        if not cp or not opportunities:
            return {"outreach_messages": []}

        top_opp = opportunities[0]
        session_id = state.get("session_id", "unknown")
        customer_name = state.get("customer_name", "Valued Customer")
        rm_name = state.get("rm_name", "Your Relationship Manager")

        # Retrieve persona tone guidelines from RAG
        tone_guidelines = await self._get_tone_guidelines(cp.persona_type.value)

        # Explanation summary — first 400 chars of the full explanation card
        explanation_summary = (explanation or "Personalised opportunity identified.")[:400]

        messages: list[OutreachMessage] = []
        channels = state.get("requested_channels") or _DEFAULT_CHANNELS

        for channel in channels:
            prompt_key = {
                OutreachChannel.WHATSAPP: PromptKey.OUTREACH_WHATSAPP,
                OutreachChannel.SMS:      PromptKey.OUTREACH_SMS,
                OutreachChannel.EMAIL:    PromptKey.OUTREACH_EMAIL,
            }.get(channel, PromptKey.OUTREACH_WHATSAPP)

            prompt = render_prompt(
                prompt_key,
                persona_type=cp.persona_type.value,
                event_type=top_opp.event_type.value,
                product_type=top_opp.product_recommended.value,
                explanation_summary=explanation_summary,
                tone_guidelines=tone_guidelines,
                customer_name=customer_name,
                rm_name=rm_name,
                bank_name="RM Copilot Bank",
            )

            # PII pre-flight
            self.assert_no_pii_in_prompt(prompt)

            raw_message = await get_llm_router().call_primary(
                prompt=prompt,
                system="You are a professional banking relationship manager. You must address the customer by their actual name and sign off with your actual name as specified in the context.",
                session_id=session_id,
                temperature=0.5,
            )

            import json
            try:
                cleaned_raw = raw_message.strip()
                if cleaned_raw.startswith("```json"):
                    cleaned_raw = cleaned_raw[7:]
                if cleaned_raw.endswith("```"):
                    cleaned_raw = cleaned_raw[:-3]
                cleaned_raw = cleaned_raw.strip()
                parsed_json = json.loads(cleaned_raw)
                opt_a = parsed_json.get("option_a", "").strip()
                opt_b = parsed_json.get("option_b", "").strip()
            except Exception as e:
                logger.warning("failed_to_parse_outreach_json", error=str(e), raw_message=raw_message)
                opt_a = raw_message
                opt_b = raw_message

            # Scan for leaked PII vault tokens
            cleaned_opt_a, safe_a = self._sanitise_message(opt_a)
            cleaned_opt_b, safe_b = self._sanitise_message(opt_b)
            cleaned_message = cleaned_opt_a
            pii_safe = safe_a and safe_b

            msg = OutreachMessage(
                channel=channel,
                message_body=cleaned_message,
                persona_tone=cp.persona_type.value,
                pii_safe=pii_safe,
                opportunity_id=top_opp.db_opportunity_id,
                product_type=top_opp.product_recommended,
                option_a=cleaned_opt_a,
                option_b=cleaned_opt_b,
            )
            messages.append(msg)
            logger.info(
                "outreach_message_generated",
                channel=channel.value,
                pii_safe=pii_safe,
                session_id=session_id,
            )

        return {"outreach_messages": messages}

    async def _get_tone_guidelines(self, persona_type: str) -> str:
        """Retrieve persona-specific tone guidelines from RAG persona_playbooks collection."""
        try:
            from services.orchestrator.tools.vector_tools import hybrid_search
            results = await hybrid_search(
                query=f"{persona_type} communication tone banking relationship manager",
                collection="persona_playbooks",
                top_k=2,
                db=self._db,
                redis_client=self._redis,
            )
            if results:
                return "\n".join(r.get("content", "")[:300] for r in results)
        except Exception as exc:
            logger.warning("tone_rag_retrieval_failed", error=str(exc))

        # Fallback tone guidelines
        return (
            "Professional yet warm. Focus on how the product solves a real need. "
            "Avoid jargon. Be concise and respectful of the customer's time."
        )

    def _sanitise_message(self, message: str) -> tuple[str, bool]:
        """
        Scan message for PII vault tokens and replace with natural placeholders.
        Returns (cleaned_message, pii_safe).
        pii_safe is False only if vault tokens were found and replaced.
        """
        tokens_found = _VAULT_TOKEN_PATTERN.findall(message)
        if not tokens_found:
            return message, True

        # Replace each vault token with a natural placeholder
        cleaned = _VAULT_TOKEN_PATTERN.sub("[Customer]", message)
        logger.warning(
            "pii_tokens_found_in_outreach",
            token_count=len(tokens_found),
            tokens=tokens_found,
        )
        return cleaned, False  # pii_safe=False signals the issue for review
