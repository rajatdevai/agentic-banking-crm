"""
Outreach endpoints -- generate, approve, and track personalized messages.

All outreach is a two-step process:
    1. POST /outreach/generate/stream  -> SSE stream of gpt-4o tokens, saves campaign on completion
    2. POST /outreach/{campaign_id}/approve -> RM reviews/edits, queues Celery dispatch task

POST /outreach/generate (blocking) is kept for backward compat.
RM approval is mandatory before any message is sent.

Routes:
    POST /outreach/generate/stream          -> streaming SSE token generation (primary)
    POST /outreach/generate                 -> blocking generate (backward compat)
    POST /outreach/{campaign_id}/approve    -> RM approves, Celery queues dispatch
    GET  /outreach/{campaign_id}/status     -> delivery funnel status
    GET  /outreach                          -> list all campaigns (catered portfolio)
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
    OutreachCampaignItem,
    OutreachCampaignsListResponse,
)
from shared.constants.enums import OpportunityStatus, OutreachChannel
from shared.db.models import Customer, Opportunity, OutreachCampaign, RelationshipManager
from shared.db.session import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/outreach", tags=["Outreach"])


# ---------------------------------------------------------------------------
# Shared helper -- build agent state from request (used by both endpoints)
# ---------------------------------------------------------------------------
async def _build_outreach_state(
    body: OutreachGenerateRequest,
    request: Request,
    current_rm: RelationshipManager,
    db: AsyncSession,
) -> dict:
    """
    Validate ownership, load profile/opportunity, build the AgentState dict.
    Raises HTTPException on validation errors.
    """
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

    from shared.db.models import CustomerProfile as CustomerProfileORM
    profile_result = await db.execute(
        select(CustomerProfileORM).where(CustomerProfileORM.customer_id == customer.id)
    )
    customer_profile_orm = profile_result.scalar_one_or_none()

    from shared.constants.enums import EventType
    from shared.models.agent_state import CustomerProfile, Opportunity as StateOpportunity

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

    resolved_event_type = None
    if opportunity.event:
        resolved_event_type = opportunity.event.event_type
    else:
        from shared.db.models import DetectedEvent
        ev_res = await db.execute(
            select(DetectedEvent)
            .where(DetectedEvent.customer_id == customer.id)
            .order_by(DetectedEvent.detected_at.desc())
            .limit(1)
        )
        latest_event = ev_res.scalar_one_or_none()
        resolved_event_type = latest_event.event_type if latest_event else EventType.MEDICAL

    opp_state = StateOpportunity(
        customer_id=str(opportunity.customer_id),
        event_type=resolved_event_type,
        product_recommended=opportunity.product_recommended,
        priority_score=float(opportunity.priority_score),
        conversion_probability=float(opportunity.conversion_prob),
        revenue_potential=float(opportunity.revenue_potential) if opportunity.revenue_potential else None,
        risk_flags=opportunity.risk_flags,
        scoring_method="database",
        db_opportunity_id=str(opportunity.id),
    )

    session_id = request.headers.get("X-Session-ID") or str(uuid.uuid4())
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))

    return {
        "customer": customer,
        "opportunity": opportunity,
        "state": {
            "customer_id": str(customer.id),
            "customer_name": customer.name or "Valued Customer",
            "rm_id": str(customer.rm_id),
            "rm_name": current_rm.name,
            "session_id": session_id,
            "trace_id": trace_id,
            "customer_profile": profile_state,
            "opportunities": [opp_state],
            "explanation": opportunity.explanation,
            "recommended_products": [],
            "requested_channels": [body.channel],
        },
    }


# ---------------------------------------------------------------------------
# POST /outreach/generate/stream  (SSE streaming -- primary path for the UI)
# ---------------------------------------------------------------------------
@router.post(
    "/generate/stream",
    summary="Stream outreach generation token-by-token via SSE",
    description=(
        "Streams gpt-4o tokens as Server-Sent Events. "
        "Events: {type:'status'}, {type:'token', token:'...'}, "
        "{type:'done', campaign_id, option_a, option_b}. "
        "Campaign is saved after all tokens are generated."
    ),
)
async def generate_outreach_stream(
    body: OutreachGenerateRequest,
    request: Request,
    current_rm: RelationshipManager = Depends(get_current_rm),
    db: AsyncSession = Depends(get_db),
):
    import json as _json
    import asyncio
    import re
    from fastapi.responses import StreamingResponse
    from services.orchestrator.agents.outreach_gen import OutreachGenAgent
    from services.orchestrator.llm.prompt_registry import PromptKey, render_prompt
    from services.orchestrator.llm.router import get_llm_router
    from shared.constants.enums import OutreachChannel

    _VAULT_TOKEN_PATTERN = re.compile(r"<PII_[A-Z0-9_]+>")

    ctx = await _build_outreach_state(body, request, current_rm, db)
    customer = ctx["customer"]
    opportunity = ctx["opportunity"]
    state = ctx["state"]

    redis = request.app.state.redis
    cp = state["customer_profile"]
    top_opp = state["opportunities"][0]
    session_id = state["session_id"]
    customer_name = state["customer_name"]
    rm_name = state["rm_name"]

    # Tone guidelines -- fast with cosine reranker
    agent = OutreachGenAgent(db=db, redis=redis)
    tone_guidelines = await agent._get_tone_guidelines(cp.persona_type.value)
    explanation_summary = (opportunity.explanation or "Personalised opportunity identified.")[:400]

    # Queue for merging streams
    queue = asyncio.Queue()

    # Define the worker for a single channel
    async def run_channel_stream(ch: OutreachChannel):
        prompt_key = {
            OutreachChannel.WHATSAPP: PromptKey.OUTREACH_WHATSAPP,
            OutreachChannel.SMS:      PromptKey.OUTREACH_SMS,
            OutreachChannel.EMAIL:    PromptKey.OUTREACH_EMAIL,
        }.get(ch, PromptKey.OUTREACH_WHATSAPP)

        prompt = render_prompt(
            prompt_key,
            persona_type=cp.persona_type.value,
            event_type=top_opp.event_type.value,
            product_type=top_opp.product_recommended.value,
            explanation_summary=explanation_summary,
            tone_guidelines=tone_guidelines,
            customer_name=customer_name,
            rm_name=rm_name,
            bank_name="RM Copilot Bank",
        )

        accumulated = ""
        try:
            async for token, is_done in get_llm_router().stream_primary(
                prompt=prompt,
                system=(
                    "You are a professional banking relationship manager. "
                    "You must address the customer by their actual name and sign off with your actual name. "
                    "Return ONLY valid JSON with option_a and option_b keys."
                ),
                session_id=session_id,
            ):
                if is_done:
                    break
                accumulated += token
                await queue.put({
                    "channel": ch.value,
                    "type": "token",
                    "token": token
                })
                await asyncio.sleep(0)

            await queue.put({
                "channel": ch.value,
                "type": "done_raw",
                "accumulated": accumulated
            })
        except Exception as exc:
            logger.error("outreach_channel_stream_error", channel=ch.value, error=str(exc))
            await queue.put({
                "channel": ch.value,
                "type": "error",
                "message": str(exc)
            })

    # Start the tasks for all three channels in parallel
    channels = [OutreachChannel.WHATSAPP, OutreachChannel.EMAIL, OutreachChannel.SMS]
    for ch in channels:
        asyncio.create_task(run_channel_stream(ch))

    async def event_stream():
        yield f"data: {_json.dumps({'type': 'status', 'message': 'Generating outreach options for all channels...'})}\n\n"

        finished_count = 0
        while finished_count < len(channels):
            msg = await queue.get()
            ch_val = msg["channel"]
            m_type = msg["type"]

            if m_type == "token":
                yield f"data: {_json.dumps({'channel': ch_val, 'type': 'token', 'token': msg['token']})}\n\n"
            elif m_type == "done_raw":
                accum = msg["accumulated"]
                # Parse options from accumulated JSON
                opt_a, opt_b = accum, accum
                try:
                    cleaned = accum.strip()
                    if cleaned.startswith("```json"):
                        cleaned = cleaned[7:]
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3]
                    parsed = _json.loads(cleaned.strip())
                    opt_a = parsed.get("option_a", accum).strip()
                    opt_b = parsed.get("option_b", accum).strip()
                except Exception:
                    pass

                opt_a = _VAULT_TOKEN_PATTERN.sub("[Customer]", opt_a)
                opt_b = _VAULT_TOKEN_PATTERN.sub("[Customer]", opt_b)

                # Save campaign using DB session sequentially (safe from concurrency)
                campaign = OutreachCampaign(
                    opportunity_id=opportunity.id,
                    channel=OutreachChannel(ch_val),
                    message_body=opt_a,
                    message_option_a=opt_a,
                    message_option_b=opt_b,
                    persona_tone=customer.persona_type.value,
                    session_id=session_id,
                )
                db.add(campaign)
                await db.commit()
                await db.refresh(campaign)

                logger.info(
                    "outreach_stream_generated",
                    rm_id=str(current_rm.id),
                    customer_id=str(customer.id),
                    campaign_id=str(campaign.id),
                    channel=ch_val,
                )

                yield f"data: {_json.dumps({
                    'channel': ch_val,
                    'type': 'done',
                    'campaign_id': str(campaign.id),
                    'option_a': opt_a,
                    'option_b': opt_b
                })}\n\n"
                finished_count += 1
            elif m_type == "error":
                yield f"data: {_json.dumps({'channel': ch_val, 'type': 'error', 'message': msg['message']})}\n\n"
                finished_count += 1

            queue.task_done()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /outreach/generate  (blocking -- kept for backward compat)
# ---------------------------------------------------------------------------
@router.post(
    "/generate",
    response_model=OutreachPreviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a personalized outreach message draft (blocking)",
    description=(
        "Runs the OutreachGenAgent pipeline (RAG persona lookup + gpt-4o generation). "
        "Returns a draft message for RM review. Message is NOT sent until the RM "
        "explicitly approves via POST /outreach/{campaign_id}/approve. "
        "For live token streaming use POST /outreach/generate/stream."
    ),
)
async def generate_outreach(
    body: OutreachGenerateRequest,
    request: Request,
    current_rm: RelationshipManager = Depends(get_current_rm),
    db: AsyncSession = Depends(get_db),
):
    ctx = await _build_outreach_state(body, request, current_rm, db)
    customer = ctx["customer"]
    opportunity = ctx["opportunity"]
    state = ctx["state"]

    redis = request.app.state.redis
    from services.orchestrator.agents.outreach_gen import OutreachGenAgent
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
    option_a = messages[0].option_a
    option_b = messages[0].option_b

    campaign = OutreachCampaign(
        opportunity_id=opportunity.id,
        channel=body.channel,
        message_body=generated_message,
        message_option_a=option_a,
        message_option_b=option_b,
        persona_tone=customer.persona_type.value,
        session_id=state["session_id"],
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
        message_option_a=option_a,
        message_option_b=option_b,
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
        "Returns 202 Accepted -- actual send is asynchronous."
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

    if body.edited_message:
        campaign.message_body = body.edited_message

    session_id = request.headers.get("X-Session-ID") or getattr(request.state, "session_id", None)
    if session_id:
        campaign.session_id = session_id

    await db.commit()

    # Invalidate outreach list cache
    redis = getattr(request.app.state, "redis", None)
    if redis:
        try:
            await redis.delete(f"outreach:list:{current_rm.id}")
        except Exception as exc:
            logger.warning("failed_to_delete_outreach_cache", error=str(exc))

    from services.workers.tasks.outreach_dispatch import dispatch_campaign
    dispatch_campaign.delay(str(campaign_id))

    logger.info(
        "outreach_approved_by_rm",
        rm_id=str(current_rm.id),
        campaign_id=str(campaign_id),
        edited=body.edited_message is not None,
        session_id=session_id,
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


# ---------------------------------------------------------------------------
# GET /outreach
# ---------------------------------------------------------------------------
@router.get(
    "",
    response_model=OutreachCampaignsListResponse,
    summary="Get all outreach campaigns (catered portfolio) owned by this RM",
)
async def list_outreach_campaigns(
    request: Request,
    current_rm: RelationshipManager = Depends(get_current_rm),
    db: AsyncSession = Depends(get_db),
):
    import json

    redis = getattr(request.app.state, "redis", None)
    CACHE_KEY = f"outreach:list:{current_rm.id}"
    CACHE_TTL = 120  # 2-minute cache

    if redis:
        try:
            cached = await redis.get(CACHE_KEY)
            if cached:
                data = json.loads(cached)
                return OutreachCampaignsListResponse(
                    campaigns=[OutreachCampaignItem(**c) for c in data]
                )
        except Exception:
            pass

    result = await db.execute(
        select(OutreachCampaign, Opportunity, Customer)
        .join(Opportunity, OutreachCampaign.opportunity_id == Opportunity.id)
        .join(Customer, Opportunity.customer_id == Customer.id)
        .where(
            Customer.rm_id == current_rm.id,
            Customer.deleted_at.is_(None),
        )
        .order_by(OutreachCampaign.sent_at.desc(), OutreachCampaign.id.desc())
        .limit(200)
    )
    rows = result.all()

    campaigns = []
    for campaign, opportunity, customer in rows:
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

        campaigns.append(
            OutreachCampaignItem(
                campaign_id=campaign.id,
                customer_id=customer.id,
                customer_name=customer.name or "Anonymous",
                opportunity_id=opportunity.id,
                product_recommended=opportunity.product_recommended.value,
                channel=campaign.channel,
                message_body=campaign.message_body,
                status=delivery_status,
                sent_at=campaign.sent_at,
                delivered_at=campaign.delivered_at,
                opened_at=campaign.opened_at,
                converted_at=campaign.converted_at,
            )
        )

    if redis:
        try:
            payload = [c.model_dump(mode="json") for c in campaigns]
            await redis.setex(CACHE_KEY, CACHE_TTL, json.dumps(payload, default=str))
        except Exception:
            pass

    return OutreachCampaignsListResponse(campaigns=campaigns)
