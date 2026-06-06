"""
ExplainabilityAgent — LLM call #1 (gpt-4o).

Reads: customer_profile, detected_events, opportunities, recommended_products from state
Writes: explanation (str — structured explanation card)

Constructs a masked prompt, calls gpt-4o, validates output through output_parser.
The explanation contains: why selected, event explanation, product rationale,
conversion reasoning, and RM action with timeframe.
"""

from __future__ import annotations

from typing import Optional

import structlog
from pydantic import BaseModel, Field

from services.orchestrator.agents.base_agent import BaseAgent
from services.orchestrator.graph.state import AgentState
from services.orchestrator.llm.output_parser import parse_llm_output
from services.orchestrator.llm.prompt_registry import PromptKey, render_prompt
from services.orchestrator.llm.router import get_llm_router
from shared.models.agent_state import (
    CustomerProfile,
    DetectedEvent,
    Opportunity,
    ProductRecommendation,
)

logger = structlog.get_logger(__name__)


class ExplainabilityOutput(BaseModel):
    """Structured schema for explainability agent output."""
    why_selected: str = Field(..., description="Why this customer was flagged")
    event_explanation: str = Field(..., description="Life event explanation")
    product_rationale: str = Field(..., description="Why this product fits")
    conversion_reasoning: str = Field(..., description="Why conversion probability is at this level")
    rm_action: str = Field(..., description="What the RM should do and when")


class ExplainabilityAgent(BaseAgent):
    agent_name = "ExplainabilityAgent"
    timeout_seconds = 45.0   # LLM calls can take up to 30s under load

    async def execute(self, state: AgentState) -> dict:
        cp: Optional[CustomerProfile] = state.get("customer_profile")
        events: list[DetectedEvent] = state.get("detected_events") or []
        opportunities: list[Opportunity] = state.get("opportunities") or []
        products: list[ProductRecommendation] = state.get("recommended_products") or []

        if not cp or not events or not opportunities:
            return {"explanation": None}

        # Use the top-ranked opportunity + its matching event
        top_opp = opportunities[0]
        matching_event = next(
            (e for e in events if e.event_type == top_opp.event_type),
            events[0]
        )
        matching_product = next(
            (p for p in products if p.product_type == top_opp.product_recommended),
            None
        )

        # Build signals summary (safe to include — no raw PII)
        signals_summary = ", ".join(
            f"{k}: {v}"
            for k, v in matching_event.signals.items()
            if k not in ("rules_fired",)
        )[:300]

        # Construct masked prompt via Jinja2 registry
        prompt = render_prompt(
            PromptKey.EXPLAINABILITY,
            persona_type=cp.persona_type.value,
            salary_band=cp.salary_band(),
            relationship_tenure_months=cp.relationship_tenure_months,
            risk_tier=cp.risk_tier.value,
            credit_score=cp.credit_score,
            behavioral_tags=cp.behavioral_tags,
            event_type=matching_event.event_type.value,
            event_confidence=matching_event.confidence_score,
            signals_summary=signals_summary,
            product_recommended=top_opp.product_recommended.value,
            conversion_probability=top_opp.conversion_probability,
            revenue_potential=top_opp.revenue_potential,
        )

        # PII pre-flight — must never contain raw PII
        self.assert_no_pii_in_prompt(prompt)

        session_id = state.get("session_id", "unknown")
        raw_output = await get_llm_router().call_primary(
            prompt=prompt,
            session_id=session_id,
        )

        # Parse and validate structured output
        output: ExplainabilityOutput = await parse_llm_output(
            raw_text=raw_output,
            response_model=ExplainabilityOutput,
            session_id=session_id,
        )

        # Compose human-readable explanation card
        explanation = (
            f"**Why Selected:** {output.why_selected}\n\n"
            f"**Life Event:** {output.event_explanation}\n\n"
            f"**Product Fit:** {output.product_rationale}\n\n"
            f"**Conversion Outlook:** {output.conversion_reasoning}\n\n"
            f"**Recommended Action:** {output.rm_action}"
        )

        # Track token usage (approximate — actual tokens from API response used in LLM router logs)
        current_tokens = state.get("llm_tokens_used", 0)
        logger.info("explainability_complete", session_id=session_id)

        return {
            "explanation": explanation,
            "llm_tokens_used": current_tokens + len(prompt.split()) * 2,  # rough estimate
        }
