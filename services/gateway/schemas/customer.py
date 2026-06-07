"""
Pydantic v2 request/response schemas for customer-related endpoints.

Security rule: external_cbs_id NEVER appears in any response schema.
That field is encrypted in the DB and is only read internally by agents.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.constants.enums import KYCStatus, PersonaType, RiskTier


class CustomerSummaryResponse(BaseModel):
    """Lightweight customer card shown in the priority queue list."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    name: Optional[str] = None
    persona_type: PersonaType
    risk_tier: RiskTier
    kyc_status: KYCStatus
    relationship_tenure_months: int

    # From customer_profiles (may be None if profile not yet refreshed)
    credit_score: Optional[int] = None
    avg_balance_3m: Optional[float] = None
    behavioral_tags: list[str] = Field(default_factory=list)


class CustomerProfileResponse(BaseModel):
    """Full customer profile — returned by GET /customers/{customer_id}."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    rm_id: uuid.UUID
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    persona_type: PersonaType
    risk_tier: RiskTier
    kyc_status: KYCStatus
    relationship_tenure_months: int

    # Financial snapshot
    salary_avg_3m: Optional[float] = None
    avg_balance_3m: Optional[float] = None
    total_investments: Optional[float] = None
    total_liabilities: Optional[float] = None
    credit_score: Optional[int] = None

    # Enriched attributes
    product_holdings: dict = Field(default_factory=dict)
    behavioral_tags: list[str] = Field(default_factory=list)
    last_refreshed_at: Optional[datetime] = None

    # NOTE: external_cbs_id is intentionally excluded — never expose to API consumers


class PriorityQueueResponse(BaseModel):
    """Response for GET /customers/priority-queue."""

    customers: list[CustomerSummaryResponse]
    total: int
    cached: bool = False
    cache_age_seconds: Optional[int] = None

