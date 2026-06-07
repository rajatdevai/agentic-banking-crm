"""
LangGraph StateGraph builder — the orchestration engine.

Graph topology (scoring pipeline):
    START
      └─ customer_intel
           ├─ transaction_intel (fan-out, parallel)
           └─ event_detection  (fan-out, parallel)
                ├─ [if no events] → no_opportunity_node → END
                └─ [if events]    → risk_assessment
                     ├─ [if high risk + low confidence] → manual_review_node → END
                     └─ [else] → opportunity_scoring
                                     └─ product_rec
                                          └─ explainability
                                               └─ outreach_gen → END

Separate entry point (copilot chat):
    START → rm_copilot → END

Graph checkpointing:
    Redis-backed via get_checkpointer(). Every step is saved.
    Errors in any agent are captured into state.errors — graph continues.

Usage:
    from services.orchestrator.graph.builder import build_scoring_graph, build_copilot_graph

    scoring_graph = build_scoring_graph(db=session, redis=redis_client)
    result_state = await scoring_graph.ainvoke(
        initial_state(customer_id=..., rm_id=..., session_id=..., trace_id=...)
    )
"""

from __future__ import annotations

import structlog
from langgraph.graph import END, START, StateGraph

from services.orchestrator.agents.customer_intel import CustomerIntelAgent
from services.orchestrator.agents.event_detection import EventDetectionAgent
from services.orchestrator.agents.explainability import ExplainabilityAgent
from services.orchestrator.agents.opportunity_scoring import OpportunityScoringAgent
from services.orchestrator.agents.outreach_gen import OutreachGenAgent
from services.orchestrator.agents.product_rec import ProductRecAgent
from services.orchestrator.agents.risk_assessment import RiskAssessmentAgent
from services.orchestrator.agents.rm_copilot import RMCopilotAgent
from services.orchestrator.agents.transaction_intel import TransactionIntelAgent
from services.orchestrator.graph.checkpointer import get_checkpointer
from services.orchestrator.graph.router import (
    route_after_event_detection,
    route_after_risk_assessment,
)
from services.orchestrator.graph.state import AgentState

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Terminal node functions (no-agent branches)
# ---------------------------------------------------------------------------

async def no_opportunity_node(state: AgentState) -> dict:
    """
    Reached when EventDetectionAgent found no life events.
    Generates a friendly summary for the RM rather than empty UI.
    """
    logger.info("no_opportunity_branch", customer_id=state.get("customer_id"))
    return {
        "explanation": (
            "No significant life events detected for this customer based on their "
            "recent transaction history. Consider scheduling a regular check-in to "
            "understand their current needs and financial goals."
        )
    }


async def manual_review_node(state: AgentState) -> dict:
    """
    Reached when risk is HIGH and event confidence is low.
    Flags opportunity for manual RM review instead of automated outreach.
    """
    logger.info("manual_review_branch", customer_id=state.get("customer_id"))
    risk = state.get("risk_assessment")
    return {
        "explanation": (
            f"This customer has been flagged for manual review. "
            f"Risk tier: {risk.risk_tier.value if risk else 'unknown'}. "
            f"Event confidence is below threshold for automated outreach. "
            f"Please review the customer's profile and risk flags before proceeding."
        )
    }


# ---------------------------------------------------------------------------
# Node wrapper factory
# ---------------------------------------------------------------------------
def _make_node(agent: object):
    """
    Create a LangGraph node function that calls agent.run(state).
    run() is the BaseAgent-wrapped execute() with retry, timeout, and error capture.
    """
    async def node_fn(state: AgentState) -> dict:
        return await agent.run(state)
    node_fn.__name__ = agent.agent_name
    return node_fn


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------

def build_scoring_graph(
    db=None,
    redis=None,
    checkpointer=None,
) -> StateGraph:
    """
    Build and compile the full customer scoring pipeline graph.

    Args:
        db: AsyncSession injected into all DB-dependent agents
        redis: Redis client for caching and checkpointing
        checkpointer: Optional pre-built checkpointer (for testing)

    Returns:
        Compiled LangGraph application ready for ainvoke()
    """
    # Instantiate all agents with shared dependencies
    customer_intel_agent   = CustomerIntelAgent(db=db, redis=redis)
    transaction_intel_agent = TransactionIntelAgent(db=db, redis=redis)
    event_detection_agent  = EventDetectionAgent(db=db, redis=redis)
    risk_assessment_agent  = RiskAssessmentAgent(db=db, redis=redis)
    opportunity_agent      = OpportunityScoringAgent(db=db, redis=redis)
    product_rec_agent      = ProductRecAgent(db=db, redis=redis)
    explainability_agent   = ExplainabilityAgent(db=db, redis=redis)
    outreach_gen_agent     = OutreachGenAgent(db=db, redis=redis)

    # Build graph
    builder = StateGraph(AgentState)

    # --- Register nodes ---
    builder.add_node("customer_intel",    _make_node(customer_intel_agent))
    builder.add_node("transaction_intel", _make_node(transaction_intel_agent))
    builder.add_node("event_detection",   _make_node(event_detection_agent))
    builder.add_node("risk_assessment_agent", _make_node(risk_assessment_agent))
    builder.add_node("opportunity_scoring", _make_node(opportunity_agent))
    builder.add_node("product_rec",       _make_node(product_rec_agent))
    builder.add_node("explainability",    _make_node(explainability_agent))
    builder.add_node("outreach_gen",      _make_node(outreach_gen_agent))
    builder.add_node("no_opportunity_node", no_opportunity_node)
    builder.add_node("manual_review_node",  manual_review_node)

    # --- Entry point ---
    builder.add_edge(START, "customer_intel")

    # --- Sequential execution of intel and event detection ---
    builder.add_edge("customer_intel", "transaction_intel")
    builder.add_edge("transaction_intel", "event_detection")

    # --- Conditional edge on event_detection output ---
    builder.add_conditional_edges(
        "event_detection",
        route_after_event_detection,
        {
            "risk_assessment": "risk_assessment_agent",
            "no_opportunity_node": "no_opportunity_node",
        },
    )

    # --- Conditional edge after risk assessment ---
    builder.add_conditional_edges(
        "risk_assessment_agent",
        route_after_risk_assessment,
        {
            "opportunity_scoring": "opportunity_scoring",
            "manual_review_node": "manual_review_node",
        },
    )

    # --- Sequential pipeline ---
    builder.add_edge("opportunity_scoring", "product_rec")
    builder.add_edge("product_rec",          "explainability")
    builder.add_edge("explainability",       "outreach_gen")

    # --- Terminal edges ---
    builder.add_edge("outreach_gen",         END)
    builder.add_edge("no_opportunity_node",  END)
    builder.add_edge("manual_review_node",   END)

    # Compile with checkpointing
    cp = checkpointer or get_checkpointer(redis)
    graph = builder.compile(checkpointer=cp)

    logger.info("scoring_graph_compiled")
    return graph


def build_copilot_graph(
    db=None,
    redis=None,
    checkpointer=None,
) -> StateGraph:
    """
    Build and compile the RM Copilot conversational graph.
    Separate entry point — does not run the scoring pipeline.

    Args:
        db: AsyncSession
        redis: Redis client
        checkpointer: Optional pre-built checkpointer

    Returns:
        Compiled LangGraph application for streaming copilot responses.
    """
    rm_copilot_agent = RMCopilotAgent(db=db, redis=redis)

    builder = StateGraph(AgentState)
    builder.add_node("rm_copilot", _make_node(rm_copilot_agent))
    builder.add_edge(START, "rm_copilot")
    builder.add_edge("rm_copilot", END)

    cp = checkpointer or get_checkpointer(redis)
    graph = builder.compile(checkpointer=cp)

    logger.info("copilot_graph_compiled")
    return graph
