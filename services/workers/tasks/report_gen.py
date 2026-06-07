# Morning digest report generation Celery task — runs at 6 AM daily.
# Generates an RM-specific digest: top 10 priority customers today,
# yesterday's outreach delivery stats, new events detected overnight.
# Pushes to RM dashboard (WebSocket) and optionally sends via email.

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone, timedelta

import structlog

from services.workers.celery_app import app

logger = structlog.get_logger(__name__)


@app.task(
    name="services.workers.tasks.report_gen.generate_morning_reports",
    queue="scoring",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    soft_time_limit=1800,
    time_limit=2100,
    acks_late=True,
)
def generate_morning_reports(self):
    """6 AM daily: generate and cache the RM morning intelligence digest."""
    try:
        return asyncio.run(_generate_morning_reports_async())
    except Exception as exc:
        logger.error("morning_reports_failed", error=str(exc))
        raise self.retry(exc=exc)


async def _generate_morning_reports_async() -> dict:
    """Async implementation of morning report generation."""
    start = time.monotonic()
    logger.info("morning_reports_generation_started")

    db_session = None
    redis_client = None
    rms_processed = 0
    rms_failed = 0

    try:
        db_session, redis_client = await _get_dependencies()

        from sqlalchemy import select
        from shared.db.models import RelationshipManager

        # Fetch active RMs
        result = await db_session.execute(
            select(RelationshipManager).where(
                RelationshipManager.is_active == True,
                RelationshipManager.deleted_at.is_(None),
            )
        )
        rms = result.scalars().all()
        logger.info("active_rms_found", count=len(rms))

        for rm in rms:
            try:
                report = await _build_report_for_rm(rm.id, db_session)
                if redis_client:
                    key = f"morning_digest:{rm.id}"
                    # Cache in Redis with a 24h TTL (86400 seconds)
                    await redis_client.setex(key, 86400, json.dumps(report))
                rms_processed += 1
            except Exception as exc:
                logger.error("rm_report_generation_failed", rm_id=str(rm.id), error=str(exc))
                rms_failed += 1

        elapsed = time.monotonic() - start
        summary = {
            "rms_processed": rms_processed,
            "rms_failed": rms_failed,
            "elapsed_seconds": round(elapsed, 1),
        }
        logger.info("morning_reports_generation_complete", **summary)
        return summary

    finally:
        if db_session:
            await db_session.close()
        if redis_client:
            await redis_client.close()


async def _build_report_for_rm(rm_id: uuid.UUID, db) -> dict:
    """Build the morning digest report data structure for a single RM."""
    from sqlalchemy import select, desc, func
    from shared.db.models import Customer, Opportunity, CustomerProfile, DetectedEvent, OutreachCampaign
    from shared.constants.enums import OpportunityStatus

    now = datetime.now(timezone.utc)

    # 1. Top 10 priority customers today (by opportunity priority_score desc with explanation)
    opps_query = (
        select(Opportunity, CustomerProfile)
        .join(Customer, Opportunity.customer_id == Customer.id)
        .outerjoin(CustomerProfile, Customer.id == CustomerProfile.customer_id)
        .where(
            Customer.rm_id == rm_id,
            Customer.deleted_at.is_(None),
            Opportunity.status == OpportunityStatus.NEW,
            Opportunity.deleted_at.is_(None),
        )
        .order_by(desc(Opportunity.priority_score))
        .limit(10)
    )
    opps_result = await db.execute(opps_query)
    opps_rows = opps_result.all()

    top_customers = []
    for opp, profile in opps_rows:
        top_customers.append({
            "customer_id": str(opp.customer_id),
            "opportunity_id": str(opp.id),
            "priority_score": float(opp.priority_score),
            "product_recommended": opp.product_recommended.value,
            "explanation": opp.explanation,
            "credit_score": profile.credit_score if profile else None,
            "avg_balance_3m": float(profile.avg_balance_3m) if (profile and profile.avg_balance_3m) else None,
        })

    # 2. Count overnight events (past 12 hours)
    twelve_hours_ago = now - timedelta(hours=12)
    events_query = (
        select(func.count(DetectedEvent.id))
        .join(Customer, DetectedEvent.customer_id == Customer.id)
        .where(
            Customer.rm_id == rm_id,
            Customer.deleted_at.is_(None),
            DetectedEvent.detected_at >= twelve_hours_ago,
        )
    )
    events_result = await db.execute(events_query)
    overnight_events_count = events_result.scalar() or 0

    # 3. Compute yesterday's outreach campaigns status stats (sent, delivered, opened, converted)
    yesterday = now.date() - timedelta(days=1)
    start_time = datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc)
    end_time = datetime.combine(yesterday, datetime.max.time(), tzinfo=timezone.utc)

    campaigns_query = (
        select(
            func.count(OutreachCampaign.id).label("sent"),
            func.count(OutreachCampaign.delivered_at).label("delivered"),
            func.count(OutreachCampaign.opened_at).label("opened"),
            func.count(OutreachCampaign.converted_at).label("converted"),
        )
        .join(Opportunity, OutreachCampaign.opportunity_id == Opportunity.id)
        .join(Customer, Opportunity.customer_id == Customer.id)
        .where(
            Customer.rm_id == rm_id,
            Customer.deleted_at.is_(None),
            OutreachCampaign.sent_at >= start_time,
            OutreachCampaign.sent_at <= end_time,
        )
    )
    campaigns_result = await db.execute(campaigns_query)
    campaign_stats_row = campaigns_result.fetchone()

    campaign_stats = {
        "sent": campaign_stats_row[0] if campaign_stats_row else 0,
        "delivered": campaign_stats_row[1] if campaign_stats_row else 0,
        "opened": campaign_stats_row[2] if campaign_stats_row else 0,
        "converted": campaign_stats_row[3] if campaign_stats_row else 0,
    }

    return {
        "rm_id": str(rm_id),
        "generated_at": now.isoformat(),
        "top_customers": top_customers,
        "overnight_events_count": overnight_events_count,
        "yesterday_outreach_stats": campaign_stats,
    }


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
