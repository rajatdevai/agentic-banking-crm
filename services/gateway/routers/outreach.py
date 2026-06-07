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
from fastapi import APIRouter, Depends, HTTPException, status, Request
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
    request: Request,
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
    from sqlalchemy.orm import selectinload
    opp_result = await db.execute(
        select(Opportunity)
        .options(selectinload(Opportunity.event))
        .where(
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

    # Load customer profile snapshot
    from shared.db.models import CustomerProfile as CustomerProfileORM
    profile_result = await db.execute(
        select(CustomerProfileORM).where(CustomerProfileORM.customer_id == customer.id)
    )
    customer_profile_orm = profile_result.scalar_one_or_none()

    # Import agent contracts & state models
    from shared.constants.enums import EventType
    from shared.models.agent_state import CustomerProfile, Opportunity as StateOpportunity
    from services.orchestrator.agents.outreach_gen import OutreachGenAgent

    profile_state = CustomerProfile(
        customer_id=str(customer.id),
        rm_id=str(customer.rm_id),
        persona_type=customer.persona_type,
        risk_tier=customer.risk_tier,
        kyc_status=customer.kyc_status,
        relationship_tenure_months=customer.relationship_tenure_months,
        salary_avg_3m=float(customer_profile_orm.salary_avg_3m) if customer_profile_orm and customer_profile_orm.salary_avg_3m else None,
        avg_balance_3m=float(customer_profile_orm.avg_balance_3m) if customer_profile_orm and customer_profile_orm.avg_balance_3m else None,
        total_investments=float(customer_profile_orm.total_investments) if customer_profile_orm and customer_profile_orm.total_investments else None,
        total_liabilities=float(customer_profile_orm.total_liabilities) if customer_profile_orm and customer_profile_orm.total_liabilities else None,
        credit_score=customer_profile_orm.credit_score if customer_profile_orm else None,
        product_holdings=customer_profile_orm.product_holdings if customer_profile_orm else {},
        behavioral_tags=customer_profile_orm.behavioral_tags if customer_profile_orm else [],
        last_refreshed_at=customer_profile_orm.last_refreshed_at if customer_profile_orm else None,
    )

    opp_state = StateOpportunity(
        customer_id=str(opportunity.customer_id),
        event_type=opportunity.event.event_type if opportunity.event else EventType.TRANSACTION_ALERT,
        product_recommended=opportunity.product_recommended,
        priority_score=float(opportunity.priority_score),
        conversion_probability=float(opportunity.conversion_prob),
        revenue_potential=float(opportunity.revenue_potential) if opportunity.revenue_potential else None,
        risk_flags=opportunity.risk_flags,
        scoring_method="database",
        db_opportunity_id=str(opportunity.id),
    )

    # Build AgentState dict
    session_id = request.headers.get("X-Session-ID") or str(uuid.uuid4())
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))

    state = {
        "customer_id": str(customer.id),
        "rm_id": str(customer.rm_id),
        "session_id": session_id,
        "trace_id": trace_id,
        "customer_profile": profile_state,
        "opportunities": [opp_state],
        "explanation": opportunity.explanation,
        "recommended_products": [],
        "requested_channels": [body.channel],
    }

    redis = request.app.state.redis
    agent = OutreachGenAgent(db=db, redis=redis)
    agent_result = await agent.run(state)

    messages = agent_result.get("outreach_messages") or []
    if not messages:
        errors = agent_result.get("errors") or []
        error_msg = "; ".join(errors) if errors else "Failed to generate outreach message."
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_msg,
        )

    generated_message = messages[0].message_body

    campaign = OutreachCampaign(
        opportunity_id=opportunity.id,
        channel=body.channel,
        message_body=generated_message,
        persona_tone=customer.persona_type.value,
        session_id=session_id,
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
        message_body=generated_message,
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
    request: Request,
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

    # Capture current session ID from request state or headers
    session_id = request.headers.get("X-Session-ID") or getattr(request.state, "session_id", None)
    if session_id:
        campaign.session_id = session_id

    await db.commit()

    # Queue Celery task
    from services.workers.tasks.outreach_dispatch import dispatch_campaign
    dispatch_campaign.delay(str(campaign_id))

    logger.info(
        "outreach_approved_by_rm",
        rm_id=str(current_rm.id),
        campaign_id=str(campaign_id),
        edited=body.edited_message is not None,
        session_id=session_id
    )

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
