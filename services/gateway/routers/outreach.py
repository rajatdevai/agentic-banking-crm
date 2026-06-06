"""
Outreach endpoints — generate, approve, and track personalized messages.

All outreach is a two-step process:
    1. POST /outreach/generate → creates draft, stores in outreach_campaigns with status=pending
    2. POST /outreach/{campaign_id}/approve → RM reviews/edits, queues Celery dispatch task

RM approval is mandatory before any message is sent.
This is a non-negotiable architectural requirement.

Routes:
    POST /outreach/generate                 → run OutreachGenAgent, return draft
    POST /outreach/{campaign_id}/approve    → RM approves, Celery queues dispatch
    GET  /outreach/{campaign_id}/status     → delivery funnel status
"""

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.gateway.middleware.auth import get_current_rm, require_rm_owns_customer
from services.gateway.schemas.outreach import (
    OutreachApproveRequest,
    OutreachGenerateRequest,
    OutreachPreviewResponse,
    OutreachStatusResponse,
)
from shared.constants.enums import OpportunityStatus, OutreachChannel
from shared.db.models import Customer, Opportunity, OutreachCampaign, RelationshipManager
from shared.db.session import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/outreach", tags=["Outreach"])


# ---------------------------------------------------------------------------
# POST /outreach/generate
# ---------------------------------------------------------------------------
@router.post(
    "/generate",
    response_model=OutreachPreviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a personalized outreach message draft",
    description=(
        "Runs the OutreachGenAgent pipeline (RAG persona lookup + gpt-4o generation). "
        "Returns a draft message for RM review. Message is NOT sent until the RM "
        "explicitly approves via POST /outreach/{campaign_id}/approve."
    ),
)
async def generate_outreach(
    body: OutreachGenerateRequest,
    current_rm: RelationshipManager = Depends(get_current_rm),
    db: AsyncSession = Depends(get_db),
):
    # Validate customer ownership
    result = await db.execute(
        select(Customer).where(
            Customer.id == body.customer_id,
            Customer.rm_id == current_rm.id,
            Customer.deleted_at.is_(None),
        )
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer {body.customer_id} not found in your portfolio.",
        )

    # Validate opportunity belongs to this customer
    opp_result = await db.execute(
        select(Opportunity).where(
            Opportunity.id == body.opportunity_id,
            Opportunity.customer_id == body.customer_id,
            Opportunity.deleted_at.is_(None),
        )
    )
    opportunity = opp_result.scalar_one_or_none()
    if not opportunity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Opportunity {body.opportunity_id} not found for this customer.",
        )

    # TODO (Phase 5): invoke the full OutreachGenAgent via LangGraph
    # For now, return a placeholder that demonstrates the correct structure.
    # The agent will replace this in Phase 5.
    placeholder_message = (
        f"[DRAFT — OutreachGenAgent not yet connected — Phase 5]\n\n"
        f"Channel: {body.channel.value}\n"
        f"Opportunity: {opportunity.product_recommended}\n"
        f"Priority Score: {opportunity.priority_score}"
    )

    campaign = OutreachCampaign(
        opportunity_id=opportunity.id,
        channel=body.channel,
        message_body=placeholder_message,
        persona_tone=customer.persona_type.value,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    logger.info(
        "outreach_draft_generated",
        rm_id=str(current_rm.id),
        customer_id=str(body.customer_id),
        campaign_id=str(campaign.id),
        channel=body.channel.value,
    )

    return OutreachPreviewResponse(
        campaign_id=campaign.id,
        customer_id=body.customer_id,
        opportunity_id=body.opportunity_id,
        channel=body.channel,
        message_body=placeholder_message,
        persona_tone=customer.persona_type.value,
        generated_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# POST /outreach/{campaign_id}/approve
# ---------------------------------------------------------------------------
@router.post(
    "/{campaign_id}/approve",
    status_code=status.HTTP_202_ACCEPTED,
    summary="RM approves outreach draft for dispatch",
    description=(
        "RM confirms the generated message (with optional edits). "
        "Queues a Celery task for async dispatch via the configured channel. "
        "Returns 202 Accepted — actual send is asynchronous."
    ),
)
async def approve_outreach(
    campaign_id: uuid.UUID,
    body: OutreachApproveRequest,
    current_rm: RelationshipManager = Depends(get_current_rm),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(OutreachCampaign, Opportunity, Customer)
        .join(Opportunity, OutreachCampaign.opportunity_id == Opportunity.id)
        .join(Customer, Opportunity.customer_id == Customer.id)
        .where(
            OutreachCampaign.id == campaign_id,
            Customer.rm_id == current_rm.id,
        )
    )
    row = result.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found or not in your portfolio.",
        )

    campaign, opportunity, customer = row

    if campaign.sent_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This campaign has already been sent.",
        )

    # Apply RM edits if provided
    if body.edited_message:
        campaign.message_body = body.edited_message

    # TODO (Phase 8): queue Celery task — run_outreach_dispatch.delay(str(campaign.id))
    # For now, log the approval intent
    logger.info(
        "outreach_approved_by_rm",
        rm_id=str(current_rm.id),
        campaign_id=str(campaign_id),
        edited=body.edited_message is not None,
    )

    await db.commit()

    return {
        "message": "Outreach approved and queued for dispatch.",
        "campaign_id": str(campaign_id),
        "status": "queued",
    }


# ---------------------------------------------------------------------------
# GET /outreach/{campaign_id}/status
# ---------------------------------------------------------------------------
@router.get(
    "/{campaign_id}/status",
    response_model=OutreachStatusResponse,
    summary="Get delivery status of an outreach campaign",
)
async def get_outreach_status(
    campaign_id: uuid.UUID,
    current_rm: RelationshipManager = Depends(get_current_rm),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(OutreachCampaign, Opportunity, Customer)
        .join(Opportunity, OutreachCampaign.opportunity_id == Opportunity.id)
        .join(Customer, Opportunity.customer_id == Customer.id)
        .where(
            OutreachCampaign.id == campaign_id,
            Customer.rm_id == current_rm.id,
        )
    )
    row = result.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found.",
        )

    campaign, _, _ = row

    # Derive status string from timestamps
    if campaign.converted_at:
        delivery_status = "converted"
    elif campaign.opened_at:
        delivery_status = "opened"
    elif campaign.delivered_at:
        delivery_status = "delivered"
    elif campaign.sent_at:
        delivery_status = "sent"
    else:
        delivery_status = "pending"

    return OutreachStatusResponse(
        campaign_id=campaign.id,
        channel=campaign.channel,
        status=delivery_status,
        sent_at=campaign.sent_at,
        delivered_at=campaign.delivered_at,
        opened_at=campaign.opened_at,
        converted_at=campaign.converted_at,
        provider_message_id=campaign.provider_message_id,
    )
