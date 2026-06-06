# Shared audit log domain model.
# Every agent execution, LLM call, and RM action is recorded here.
# Audit records are write-once — no update or delete ever.
# Required retention: 7 years (regulatory mandate).

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class AuditRecord(BaseModel):
    """Immutable audit record for a single agent execution or RM action."""
    audit_id: str
    session_id: str
    agent_name: Optional[str] = None
    action_type: str            # agent_execution | rm_approval | outreach_dispatch | llm_call
    input_masked: dict = Field(default_factory=dict, description="Input with all PII already masked")
    output: dict = Field(default_factory=dict)
    llm_provider: Optional[str] = None
    model_used: Optional[str] = None
    tokens_used: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    executed_at: datetime = Field(default_factory=datetime.utcnow)
    rm_id: Optional[str] = None
