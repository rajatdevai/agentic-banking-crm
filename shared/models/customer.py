# Shared Pydantic v2 domain model for a bank customer.
# Used across: orchestrator agents, gateway schemas, workers, and ML feature pipeline.
# NOTE: this model contains NO raw PII — all identifier fields use masked tokens or UUIDs.
# Raw PII (names, phone numbers, emails) lives only in PostgreSQL with column-level encryption.

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from shared.constants.enums import PersonaType, RiskTier, KYCStatus


class CustomerProfile(BaseModel):
    """Pydantic domain model for a customer — PII-free, safe for agent state."""

    customer_id: str = Field(..., description="UUID — internal identifier, never the CBS account number")
    rm_id: str = Field(..., description="UUID of the assigned Relationship Manager")
    persona_type: PersonaType
    risk_tier: RiskTier
    kyc_status: KYCStatus
    relationship_tenure_months: int

    # Financial summary (from customer_profiles denormalised snapshot)
    salary_avg_3m: Optional[float] = None
    avg_balance_3m: Optional[float] = None
    total_investments: Optional[float] = None
    total_liabilities: Optional[float] = None
    credit_score: Optional[int] = None

    # Enriched attributes
    product_holdings: dict = Field(default_factory=dict)
    behavioral_tags: list[str] = Field(default_factory=list)
    last_refreshed_at: Optional[datetime] = None

    class Config:
        use_enum_values = True
