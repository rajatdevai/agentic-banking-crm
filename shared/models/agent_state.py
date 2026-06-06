# AgentState TypedDict — shared state object for the LangGraph DAG.
# This is the single source of truth flowing between all agent nodes.
# Every agent reads from this and writes only its own output fields back.
# Kept in shared/ so the gateway, workers, and orchestrator can all import it.

from typing import TypedDict, Optional
from shared.models.customer import CustomerProfile
from shared.models.opportunity import DetectedEvent, Opportunity


class TransactionSummary(TypedDict, total=False):
    """Aggregated transaction intelligence output from TransactionIntelAgent."""
    avg_monthly_spend: float
    top_mcc_categories: list[str]
    income_credits_3m: list[float]
    behavioral_tags: list[str]
    spend_volatility: float


class RiskAssessment(TypedDict, total=False):
    """Credit risk output from RiskAssessmentAgent."""
    risk_flag: str          # CLEAR | MONITOR | HIGH_RISK | DECLINED
    cibil_score: int
    foir_current: float
    max_eligible_amount: float
    decline_reasons: list[str]


class OutreachMessage(TypedDict, total=False):
    """Generated outreach message awaiting RM approval."""
    channel: str            # whatsapp | sms | email
    message_body: str
    persona_tone: str
    opportunity_id: str
    template_used: str


class AgentState(TypedDict, total=False):
    """
    Shared state passed between all LangGraph agent nodes.

    Lifecycle:
      - Created fresh by the orchestrator at the start of each RM request
      - Each agent reads what it needs and writes only its output fields
      - Agents never call each other directly — all coordination is via state
      - Checkpointed to Redis after each node for fault-tolerant resumption
    """
    # Identity — injected at creation, never sent raw to LLM
    customer_ids: list[str]
    rm_id: str
    session_id: str
    target_product: Optional[str]

    # Populated progressively by agents
    customer_profiles: list[CustomerProfile]
    transactions_summary: Optional[TransactionSummary]
    detected_events: list[DetectedEvent]
    risk_assessment: Optional[RiskAssessment]
    opportunities: list[Opportunity]
    outreach_messages: list[OutreachMessage]
    final_response: Optional[str]

    # Control flow
    errors: list[str]
    agent_trace: list[str]      # Ordered list of agent names that have executed
    should_skip_llm: bool       # Set True when rules alone are sufficient
