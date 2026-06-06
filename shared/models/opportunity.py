# Shared Pydantic v2 domain model for a scored customer opportunity.
# An opportunity represents: one customer + one detected event + one recommended product
# with a composite priority_score driving RM queue ordering.

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from shared.constants.enums import ProductType, OpportunityStatus


class DetectedEvent(BaseModel):
    """A life event detected from transaction analysis — fully rule-based, auditable."""
    event_id: str
    customer_id: str
    event_type: str  # Maps to EventType enum values
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    signals: dict = Field(default_factory=dict, description="Evidence: which rules fired and their inputs")
    detected_at: datetime
    expires_at: Optional[datetime] = None


class Opportunity(BaseModel):
    """A scored, ranked customer opportunity ready for RM review."""
    opportunity_id: str
    customer_id: str
    event_id: Optional[str] = None
    product_recommended: ProductType
    priority_score: float = Field(..., description="Composite: conversion_prob * revenue_potential * urgency")
    conversion_prob: float = Field(..., ge=0.0, le=1.0)
    revenue_potential: Optional[float] = None
    risk_flags: dict = Field(default_factory=dict)
    explanation: Optional[str] = None
    status: OpportunityStatus = OpportunityStatus.NEW
    created_at: Optional[datetime] = None

    class Config:
        use_enum_values = True
