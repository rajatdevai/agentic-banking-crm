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
                rm_name="Your Relationship Manager",  # RM name masked — real name from gateway context
                bank_name="RM Copilot Bank",
            )

            # PII pre-flight
            self.assert_no_pii_in_prompt(prompt)

            raw_message = await get_llm_router().call_primary(
                prompt=prompt,
                system="You are a professional banking relationship manager writing to a valued customer.",
                session_id=session_id,
                temperature=0.5,
            )

            # Scan for leaked PII vault tokens
            cleaned_message, pii_safe = self._sanitise_message(raw_message)

            msg = OutreachMessage(
                channel=channel,
                message_body=cleaned_message,
                persona_tone=cp.persona_type.value,
                pii_safe=pii_safe,
                opportunity_id=top_opp.db_opportunity_id,
                product_type=top_opp.product_recommended,
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
