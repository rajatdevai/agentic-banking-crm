"""
Daily Scoring Task — nightly batch re-scoring of all active customers.

Schedule: 2:00 AM IST every night (configured in beat_schedule.py)
Queue:    scoring
SLA:      Must complete before 6:00 AM IST (4-hour budget)

Workflow:
    1. Invalidate all feature caches
    2. Fetch all active customer IDs
    3. For each customer:
       a. Compute feature vector
       b. Run conversion model for each detected event
       c. Run churn model
       d. Compute priority_score
       e. Upsert opportunity into opportunities table
    4. Group opportunities by RM, sort by priority_score
    5. Cache top-N priority queue per RM in Redis (TTL 4 hours)
    6. Log summary metrics

Priority score formula (matches OpportunityScoringAgent):
    priority_score = (
        0.40 * conversion_probability +
        0.25 * revenue_potential_normalized +
        0.20 * urgency_factor +
        0.15 * (1 - churn_risk)
    ) * 100
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog

from services.workers.celery_app import app

logger = structlog.get_logger(__name__)

_REVENUE_POTENTIAL = {
    "personal_loan": 15_000,
    "home_loan": 80_000,
    "education_loan": 25_000,
    "working_capital_loan": 40_000,
    "wealth_advisory": 30_000,
    "gold_loan": 8_000,
    "premium_credit_card": 5_000,
    "insurance": 12_000,
    "mutual_fund": 10_000,
}
_MAX_REVENUE = max(_REVENUE_POTENTIAL.values())


@app.task(
    name="services.workers.tasks.daily_scoring.run_daily_scoring",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    soft_time_limit=14400,
    time_limit=17280,
    acks_late=True,
)
def run_daily_scoring(self):
    """
    Celery task entry point — wraps the async implementation.
    """
    try:
        return asyncio.run(_daily_scoring_async())
    except Exception as exc:
        logger.error("daily_scoring_failed", error=str(exc))
        raise self.retry(exc=exc)


async def _daily_scoring_async() -> dict:
    """Async implementation of nightly batch scoring."""
    start = time.monotonic()
    logger.info("daily_scoring_started")

    db_session = None
    redis_client = None
    processed = 0
    failed = 0
    total_customers = 0

    try:
        db_session, redis_client = await _get_dependencies()

        # Step 1: Invalidate all feature caches
        from services.ml.features.feature_store import invalidate_all_features
        cleared = await invalidate_all_features(redis_client)
        logger.info("feature_cache_cleared_for_scoring", keys_deleted=cleared)

        # Step 2: Fetch all active customers
        customer_ids = await _fetch_active_customer_ids(db_session)
        total_customers = len(customer_ids)
        logger.info("customers_to_score", count=total_customers)

        # Step 3: Score each customer
        rm_opportunities: dict[str, list[dict]] = {}  # rm_id → list of opportunity dicts

        for customer_id in customer_ids:
            try:
                opp_data = await _score_customer(customer_id, db_session, redis_client)
                if opp_data:
                    rm_id = opp_data.get("rm_id", "unknown")
                    if rm_id not in rm_opportunities:
                        rm_opportunities[rm_id] = []
                    rm_opportunities[rm_id].append(opp_data)
                    processed += 1
            except Exception as exc:
                logger.error("customer_scoring_failed", customer_id=customer_id, error=str(exc))
                failed += 1

        # Step 4: Cache priority queues per RM
        await _cache_rm_priority_queues(rm_opportunities, redis_client)

        elapsed = time.monotonic() - start
        summary = {
            "total_customers": total_customers,
            "processed": processed,
            "failed": failed,
            "elapsed_seconds": round(elapsed, 1),
        }
        logger.info("daily_scoring_complete", **summary)
        return summary

    finally:
        if db_session:
            await db_session.close()


async def _score_customer(
    customer_id: str,
    db,
    redis,
) -> Optional[dict]:
    """Score a single customer — returns opportunity data dict or None."""
    from services.ml.features.feature_store import get_or_compute_features
    from services.orchestrator.tools.scoring_tools import (
        get_conversion_probability, get_churn_probability,
    )
    from sqlalchemy import select
    from shared.db.models import Customer, DetectedEvent
    from shared.constants.enums import OpportunityStatus

    # Load features
    features = await get_or_compute_features(customer_id, redis=redis, db=db)

    # Get active events
    event_result = await db.execute(
        select(DetectedEvent.event_type, DetectedEvent.confidence_score)
        .where(
            DetectedEvent.customer_id == customer_id,
            DetectedEvent.actioned == False,  # noqa: E712
        )
        .order_by(DetectedEvent.confidence_score.desc())
        .limit(1)
    )
    event_row = event_result.fetchone()

    event_type = event_row.event_type.value if event_row else "unknown"
    event_confidence = float(event_row.confidence_score) if event_row else 0.0

    # Run models
    conversion_prob = await get_conversion_probability(
        customer_id, event_type, db=db, redis=redis
    )
    churn_prob = await get_churn_probability(customer_id, db=db, redis=redis)

    # Determine recommended product from event
    product = _event_to_product(event_type)
    revenue = _REVENUE_POTENTIAL.get(product, 10_000)
    revenue_norm = revenue / _MAX_REVENUE

    # Urgency factor — based on event confidence and days since detection
    urgency = min(event_confidence * 1.2, 1.0)

    # Priority score formula
    priority_score = (
        0.40 * conversion_prob +
        0.25 * revenue_norm +
        0.20 * urgency +
        0.15 * (1.0 - churn_prob)
    ) * 100.0

    # Get customer RM
    cust_result = await db.execute(
        select(Customer.rm_id).where(Customer.id == customer_id)
    )
    cust_row = cust_result.fetchone()
    rm_id = str(cust_row.rm_id) if cust_row else "unknown"

    # Upsert opportunity
    await _upsert_opportunity(
        customer_id=customer_id,
        event_type=event_type,
        product=product,
        priority_score=priority_score,
        conversion_prob=conversion_prob,
        revenue=float(revenue),
        churn_prob=churn_prob,
        db=db,
    )

    return {
        "customer_id": customer_id,
        "rm_id": rm_id,
        "priority_score": priority_score,
        "conversion_prob": conversion_prob,
        "churn_prob": churn_prob,
        "event_type": event_type,
        "product": product,
    }


async def _upsert_opportunity(
    customer_id: str,
    event_type: str,
    product: str,
    priority_score: float,
    conversion_prob: float,
    revenue: float,
    churn_prob: float,
    db,
) -> None:
    """Insert or update opportunity record for this customer."""
    from sqlalchemy import select
    from shared.db.models import Opportunity
    from shared.constants.enums import OpportunityStatus, ProductType

    try:
        product_enum = ProductType(product)
    except ValueError:
        product_enum = ProductType.PERSONAL_LOAN

    # Check for existing active opportunity
    existing = await db.execute(
        select(Opportunity).where(
            Opportunity.customer_id == customer_id,
            Opportunity.status == OpportunityStatus.NEW,
        ).limit(1)
    )
    opp = existing.scalar_one_or_none()

    if opp:
        opp.priority_score = priority_score
        opp.conversion_prob = conversion_prob
        opp.revenue_potential = revenue
        opp.risk_flags = {"churn_risk": round(churn_prob, 3)}
    else:
        opp = Opportunity(
            id=uuid.uuid4(),
            customer_id=uuid.UUID(customer_id),
            product_recommended=product_enum,
            priority_score=priority_score,
            conversion_prob=conversion_prob,
            revenue_potential=revenue,
            risk_flags={"churn_risk": round(churn_prob, 3)},
            status=OpportunityStatus.NEW,
        )
        db.add(opp)

    await db.commit()


async def _cache_rm_priority_queues(
    rm_opportunities: dict[str, list[dict]],
    redis,
) -> None:
    """Cache sorted priority queue per RM in Redis."""
    if redis is None:
        return

    for rm_id, opps in rm_opportunities.items():
        sorted_opps = sorted(opps, key=lambda o: o["priority_score"], reverse=True)
        key = f"priority_queue:{rm_id}"
        await redis.setex(key, 14400, json.dumps(sorted_opps[:50]))  # Top 50

    logger.info("rm_priority_queues_cached", rm_count=len(rm_opportunities))


async def _fetch_active_customer_ids(db) -> list[str]:
    from sqlalchemy import select
    from shared.db.models import Customer
    result = await db.execute(
        select(Customer.id).where(Customer.deleted_at.is_(None))
    )
    return [str(row[0]) for row in result.fetchall()]


def _event_to_product(event_type: str) -> str:
    mapping = {
        "wedding": "personal_loan",
        "home_purchase": "home_loan",
        "foreign_education": "education_loan",
        "child_education": "education_loan",
        "medical": "personal_loan",
        "business_expansion": "working_capital_loan",
        "promotion": "premium_credit_card",
        "wealth_migration": "wealth_advisory",
        "retirement_planning": "mutual_fund",
    }
    return mapping.get(event_type, "personal_loan")


async def _get_dependencies():
    """Create DB session and Redis client for the task."""
    from shared.db.session import get_async_session
    import redis.asyncio as aioredis
    from shared.config.settings import get_settings

    settings = get_settings()
    redis_client = await aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    async for session in get_async_session():
        return session, redis_client

    return None, redis_client
