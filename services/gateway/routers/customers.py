"""
Customer endpoints — the RM's primary dashboard data source.

All routes require authentication. Customer-specific routes additionally
require portfolio ownership via require_rm_owns_customer.

Routes:
    GET  /customers/priority-queue              → ranked customer list (Redis cache → DB fallback)
    GET  /customers/{customer_id}               → full customer profile
    GET  /customers/{customer_id}/opportunities → active opportunities for a customer
    POST /customers/{customer_id}/opportunities/{opportunity_id}/dismiss
"""

import json
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.gateway.middleware.auth import get_current_rm, require_rm_owns_customer
from services.gateway.schemas.customer import (
    CustomerProfileResponse,
    CustomerSummaryResponse,
    PriorityQueueResponse,
)
from services.gateway.schemas.opportunity import (
    DismissOpportunityRequest,
    OpportunityListResponse,
    OpportunityResponse,
)
from shared.constants.enums import OpportunityStatus
from shared.db.models import Customer, CustomerProfile, Opportunity, RelationshipManager
from shared.db.session import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/customers", tags=["Customers"])


# ---------------------------------------------------------------------------
# GET /customers/priority-queue
# ---------------------------------------------------------------------------
@router.get(
    "/priority-queue",
    response_model=PriorityQueueResponse,
    summary="Get RM's priority customer queue",
    description=(
        "Returns the RM's customers ranked by opportunity priority score. "
        "Served from Redis cache for fast dashboard loads. Falls back to "
        "database query on cache miss and refreshes the cache."
    ),
)
async def get_priority_queue(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200, description="Maximum customers to return"),
    current_rm: RelationshipManager = Depends(get_current_rm),
    db: AsyncSession = Depends(get_db),
):
    redis = getattr(request.app.state, "redis", None)
    cache_key = f"priority_queue:{current_rm.id}:{limit}"
    cached = False

    # Try Redis cache first
    if redis:
        try:
            raw = await redis.get(cache_key)
            if raw:
                data = json.loads(raw)
                return PriorityQueueResponse(
                    customers=[CustomerSummaryResponse(**c) for c in data["customers"]],
                    total=data["total"],
                    cached=True,
                    cache_age_seconds=data.get("age_seconds"),
                )
        except Exception as cache_exc:
            logger.warning("priority_queue_cache_miss", error=str(cache_exc))

    # DB fallback — join customers with profiles and their best opportunity score
    result = await db.execute(
        select(Customer, CustomerProfile)
        .outerjoin(CustomerProfile, Customer.id == CustomerProfile.customer_id)
        .where(
            Customer.rm_id == current_rm.id,
            Customer.deleted_at.is_(None),
        )
        .limit(limit)
    )
    rows = result.all()

    customers_out: list[CustomerSummaryResponse] = []
    for customer, profile in rows:
        customers_out.append(
            CustomerSummaryResponse(
                customer_id=customer.id,
                persona_type=customer.persona_type,
                risk_tier=customer.risk_tier,
                kyc_status=customer.kyc_status,
                relationship_tenure_months=customer.relationship_tenure_months,
                credit_score=profile.credit_score if profile else None,
                avg_balance_3m=float(profile.avg_balance_3m) if profile and profile.avg_balance_3m else None,
                behavioral_tags=profile.behavioral_tags if profile else [],
            )
        )

    response = PriorityQueueResponse(
        customers=customers_out,
        total=len(customers_out),
        cached=False,
    )

    # Populate cache for next request
    if redis:
        try:
            await redis.setex(
                cache_key,
                300,  # 5-minute cache for priority queue
                json.dumps({
                    "customers": [c.model_dump(mode="json") for c in customers_out],
                    "total": len(customers_out),
                    "age_seconds": 0,
                }),
            )
        except Exception:
            pass  # Cache write failure is non-fatal

    return response


# ---------------------------------------------------------------------------
# GET /customers/{customer_id}
# ---------------------------------------------------------------------------
@router.get(
    "/{customer_id}",
    response_model=CustomerProfileResponse,
    summary="Get full customer profile",
)
async def get_customer_profile(
    customer: Customer = Depends(require_rm_owns_customer),
    db: AsyncSession = Depends(get_db),
):
    # Load associated profile
    result = await db.execute(
        select(CustomerProfile).where(CustomerProfile.customer_id == customer.id)
    )
    profile = result.scalar_one_or_none()

    return CustomerProfileResponse(
        customer_id=customer.id,
        rm_id=customer.rm_id,
        persona_type=customer.persona_type,
        risk_tier=customer.risk_tier,
        kyc_status=customer.kyc_status,
        relationship_tenure_months=customer.relationship_tenure_months,
        salary_avg_3m=float(profile.salary_avg_3m) if profile and profile.salary_avg_3m else None,
        avg_balance_3m=float(profile.avg_balance_3m) if profile and profile.avg_balance_3m else None,
        total_investments=float(profile.total_investments) if profile and profile.total_investments else None,
        total_liabilities=float(profile.total_liabilities) if profile and profile.total_liabilities else None,
        credit_score=profile.credit_score if profile else None,
        product_holdings=profile.product_holdings if profile else {},
        behavioral_tags=profile.behavioral_tags if profile else [],
        last_refreshed_at=profile.last_refreshed_at if profile else None,
    )


# ---------------------------------------------------------------------------
# GET /customers/{customer_id}/opportunities
# ---------------------------------------------------------------------------
@router.get(
    "/{customer_id}/opportunities",
    response_model=OpportunityListResponse,
    summary="Get active opportunities for a customer",
)
async def get_customer_opportunities(
    customer: Customer = Depends(require_rm_owns_customer),
    db: AsyncSession = Depends(get_db),
    include_dismissed: bool = Query(
        default=False, description="Include dismissed opportunities in results"
    ),
):
    query = select(Opportunity).where(
        Opportunity.customer_id == customer.id,
        Opportunity.deleted_at.is_(None),
    )
    if not include_dismissed:
        query = query.where(Opportunity.status != OpportunityStatus.DISMISSED)

    query = query.order_by(Opportunity.priority_score.desc())

    result = await db.execute(query)
    opportunities = result.scalars().all()

    return OpportunityListResponse(
        opportunities=[
            OpportunityResponse(
                opportunity_id=opp.id,
                customer_id=opp.customer_id,
                event_id=opp.event_id,
                product_recommended=opp.product_recommended,
                priority_score=float(opp.priority_score),
                conversion_prob=float(opp.conversion_prob),
                revenue_potential=float(opp.revenue_potential) if opp.revenue_potential else None,
                risk_flags=opp.risk_flags,
                explanation=opp.explanation,
                status=opp.status,
                created_at=opp.created_at,
            )
            for opp in opportunities
        ],
        total=len(opportunities),
    )


# ---------------------------------------------------------------------------
# POST /customers/{customer_id}/opportunities/{opportunity_id}/dismiss
# ---------------------------------------------------------------------------
@router.post(
    "/{customer_id}/opportunities/{opportunity_id}/dismiss",
    status_code=status.HTTP_200_OK,
    summary="Dismiss an opportunity",
)
async def dismiss_opportunity(
    opportunity_id: uuid.UUID,
    body: DismissOpportunityRequest,
    customer: Customer = Depends(require_rm_owns_customer),
    current_rm: RelationshipManager = Depends(get_current_rm),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Opportunity).where(
            Opportunity.id == opportunity_id,
            Opportunity.customer_id == customer.id,
            Opportunity.deleted_at.is_(None),
        )
    )
    opportunity = result.scalar_one_or_none()

    if not opportunity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Opportunity {opportunity_id} not found for this customer.",
        )

    if opportunity.status == OpportunityStatus.DISMISSED:
        return {"message": "Opportunity already dismissed.", "opportunity_id": str(opportunity_id)}

    opportunity.status = OpportunityStatus.DISMISSED
    await db.commit()

    logger.info(
        "opportunity_dismissed",
        rm_id=str(current_rm.id),
        customer_id=str(customer.id),
        opportunity_id=str(opportunity_id),
        reason=body.reason,
    )

    return {"message": "Opportunity dismissed.", "opportunity_id": str(opportunity_id)}
