"""
Conditional edge router functions for the LangGraph graph.

All routing logic is implemented as clean named functions (not lambdas) for
testability and readability.

Graph flow:
    START
      └─ customer_intel
           └─ [fan-out] → transaction_intel + event_detection (parallel)
                └─ [merge] → risk_assessment
                     └─ opportunity_scoring
                          └─ product_rec
                               └─ explainability
                                    └─ outreach_gen
                                         └─ END

Conditional edges:
    1. After event_detection: if no events → skip_to_no_opportunity
    2. After risk_assessment: if high risk + low confidence events → skip_outreach
"""

from __future__ import annotations

from services.orchestrator.graph.state import AgentState


def route_after_event_detection(state: AgentState) -> str:
    """
    After EventDetectionAgent, decide the next step.

    Returns:
        "risk_assessment"       → events were detected, continue full pipeline
        "no_opportunity_node"   → no events detected, short-circuit to summary
    """
    events = state.get("detected_events") or []
    should_skip = state.get("should_skip_llm", False)

    if not events or should_skip:
        return "no_opportunity_node"

    return "risk_assessment"


def route_after_risk_assessment(state: AgentState) -> str:
    """
    After RiskAssessmentAgent, decide whether to skip outreach.

    Returns:
        "opportunity_scoring"   → proceed with full pipeline
        "manual_review_node"    → high risk + low event confidence → flag for RM review
    """
    from shared.constants.enums import RiskTier

    risk = state.get("risk_assessment")
    events = state.get("detected_events") or []

    if risk and risk.risk_tier == RiskTier.HIGH:
        # If ALL events have low confidence (< 0.60), skip automated outreach
        all_low_confidence = all(
            e.confidence_score < 0.60 for e in events
        ) if events else True

        if all_low_confidence:
            return "manual_review_node"

    return "opportunity_scoring"
