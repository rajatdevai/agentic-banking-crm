"""
Pydantic v2 schemas for opportunity-related endpoints.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.constants.enums import EventType, OpportunityStatus, ProductType


class DetectedEventResponse(BaseModel):
    """A life event detected from transaction analysis."""

    model_config = ConfigDict(from_attributes=True)

    event_id: uuid.UUID
    event_type: EventType
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    signals: dict = Field(default_factory=dict)
    detected_at: datetime
    expires_at: Optional[datetime] = None


class OpportunityResponse(BaseModel):
    """A scored opportunity shown in the RM priority queue with reasoning card."""

    model_config = ConfigDict(from_attributes=True)

    opportunity_id: uuid.UUID
    customer_id: uuid.UUID
    event_id: Optional[uuid.UUID] = None
    product_recommended: ProductType
    priority_score: float
    conversion_prob: float = Field(..., ge=0.0, le=1.0)
    revenue_potential: Optional[float] = None
    risk_flags: dict = Field(default_factory=dict)
    explanation: Optional[str] = None
    status: OpportunityStatus
    created_at: Optional[datetime] = None


class OpportunityListResponse(BaseModel):
    """List of opportunities for a customer."""

    opportunities: list[OpportunityResponse]
    total: int


class DismissOpportunityRequest(BaseModel):
    """Request body for dismissing an opportunity."""

    reason: Optional[str] = Field(
        None,
        description="Optional RM-provided reason for dismissal (logged for analytics)",
        max_length=500,
    )
