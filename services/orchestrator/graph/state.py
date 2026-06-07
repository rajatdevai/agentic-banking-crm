"""
AgentState — the single shared object flowing through the LangGraph graph.

Every node in the graph receives the full state and returns a dict containing
only the fields it modifies. LangGraph merges these partial dicts into the
running state automatically.

Design rules:
- TypedDict is used (not a Pydantic model or dataclass) because LangGraph's
  StateGraph requires TypedDict or Annotated TypedDict for state
- List fields use Annotated[list, operator.add] so parallel fan-out nodes
  can safely append without clobbering each other
- Every field has a clear owner (documented inline)
- State is immutable within a node — nodes return dicts, they don't mutate
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

from shared.models.agent_state import (
    CustomerProfile,
    DetectedEvent,
    Opportunity,
    OutreachMessage,
    ProductRecommendation,
    RiskAssessment,
    TransactionSummary,
)


class AgentState(TypedDict, total=False):
    """
    Central shared state for the RM Copilot LangGraph execution graph.

    Field ownership:
        customer_id, rm_id, session_id, trace_id  → set by gateway before graph entry
        customer_profile                           → CustomerIntelAgent
        transactions_summary                       → TransactionIntelAgent
        detected_events                            → EventDetectionAgent
        risk_assessment                            → RiskAssessmentAgent
        opportunities                              → OpportunityScoringAgent
        recommended_products                       → ProductRecAgent
        explanation                                → ExplainabilityAgent
        outreach_messages                          → OutreachGenAgent
        errors                                     → any agent on failure
        agent_trace                                → BaseAgent wrapper (auto-appended)
        should_skip_llm                            → EventDetectionAgent (no events → skip)
        llm_tokens_used                            → LLM router (accumulated)
        rm_question                                → set by gateway for copilot chat
        copilot_response_chunks                    → RMCopilotAgent (streaming)
    """

    # --- Request identifiers (set before graph entry) ---
    customer_id: str
    customer_name: str
    rm_id: str
    rm_name: str
    session_id: str
    trace_id: str

    # --- Agent outputs (each field owned by exactly one agent) ---
    customer_profile: Optional[CustomerProfile]
    transactions_summary: Optional[TransactionSummary]

    # Annotated with operator.add so parallel fan-out nodes can safely append
    detected_events: Annotated[list[DetectedEvent], operator.add]
    opportunities: Annotated[list[Opportunity], operator.add]
    recommended_products: Annotated[list[ProductRecommendation], operator.add]
    outreach_messages: Annotated[list[OutreachMessage], operator.add]

    risk_assessment: Optional[RiskAssessment]
    explanation: Optional[str]

    # --- RM Copilot conversational mode ---
    rm_question: Optional[str]              # Free-text question from the RM
    copilot_response_chunks: Annotated[list[str], operator.add]   # Streaming tokens
    token_queue: Optional[Any]              # asyncio.Queue for real-time SSE token streaming

    # --- Execution metadata (maintained by BaseAgent wrapper) ---
    errors: Annotated[list[str], operator.add]       # Error messages from failed agents
    agent_trace: Annotated[list[str], operator.add]  # Ordered list of agent names run

    # --- Control flow flags ---
    should_skip_llm: bool   # True when event detection found nothing → skip LLM agents
    llm_tokens_used: int    # Running total tokens across all LLM calls this session


def initial_state(
    customer_id: str,
    customer_name: str,
    rm_id: str,
    rm_name: str,
    session_id: str,
    trace_id: str,
    rm_question: Optional[str] = None,
    token_queue: Optional[Any] = None,
) -> AgentState:
    """
    Factory function for creating a fresh AgentState at graph entry.
    All list fields must be initialized to empty lists (not None) so
    Annotated[list, operator.add] reducers work correctly.
    """
    return AgentState(
        customer_id=customer_id,
        customer_name=customer_name,
        rm_id=rm_id,
        rm_name=rm_name,
        session_id=session_id,
        trace_id=trace_id,
        customer_profile=None,
        transactions_summary=None,
        detected_events=[],
        risk_assessment=None,
        opportunities=[],
        recommended_products=[],
        explanation=None,
        outreach_messages=[],
        rm_question=rm_question,
        token_queue=token_queue,
        copilot_response_chunks=[],
        errors=[],
        agent_trace=[],
        should_skip_llm=False,
        llm_tokens_used=0,
    )
