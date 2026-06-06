"""
Pydantic v2 schemas for outreach-related endpoints.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.constants.enums import OutreachChannel


class OutreachGenerateRequest(BaseModel):
    """Request body for POST /outreach/generate — triggers the OutreachGenAgent."""

    customer_id: uuid.UUID
    opportunity_id: uuid.UUID
    channel: OutreachChannel = OutreachChannel.WHATSAPP
    additional_context: Optional[str] = Field(
        None,
        description="Optional RM note to personalise the generated message further",
        max_length=500,
    )


class OutreachPreviewResponse(BaseModel):
    """Draft message returned for RM review — not yet dispatched."""

    campaign_id: uuid.UUID
    customer_id: uuid.UUID
    opportunity_id: uuid.UUID
    channel: OutreachChannel
    message_body: str
    persona_tone: Optional[str] = None
    generated_at: datetime


class OutreachApproveRequest(BaseModel):
    """
    RM approval payload. The RM can optionally edit the message before approving.
    If edited_message is provided, it replaces the generated draft before dispatch.
    """

    edited_message: Optional[str] = Field(
        None,
        description="If provided, overrides the generated message body before sending",
        max_length=4096,
    )


class OutreachStatusResponse(BaseModel):
    """Delivery status for a dispatched outreach campaign."""

    model_config = ConfigDict(from_attributes=True)

    campaign_id: uuid.UUID
    channel: OutreachChannel
    status: str  # pending | sent | delivered | opened | converted | failed
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    converted_at: Optional[datetime] = None
    provider_message_id: Optional[str] = None


class LoginRequest(BaseModel):
    """RM login credentials."""

    email: str = Field(..., description="RM email address")
    password: str = Field(..., description="RM password", min_length=8)


class TokenResponse(BaseModel):
    """JWT access token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
