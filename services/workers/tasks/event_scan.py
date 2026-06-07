"""
Event Scan Task — scans new transactions every 15 minutes.

Schedule: every 15 minutes (configured in beat_schedule.py)
Queue:    events
SLA:      Must complete within 5 minutes

Workflow:
    1. Query transactions with txn_at in the last 20 minutes (with overlap)
    2. Group by customer_id
    3. For each customer with new transactions:
       a. Run EventDetectionAgent rule engine on their recent transactions
       b. If new high-confidence event (confidence > 0.8) is detected:
          - Insert detected_event row
          - Trigger opportunity re-scoring for this customer
          - Invalidate their feature cache and priority queue cache
       c. If priority_score > 85 after scoring, push WebSocket notification

WebSocket notification:
    Publishes a Redis Pub/Sub message on channel "rm_notifications:{rm_id}"
    The gateway's SSE/WebSocket handler subscribes and pushes to the RM dashboard.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import structlog

from services.workers.celery_app import app

logger = structlog.get_logger(__name__)

_SCAN_WINDOW_MINUTES = 20    # Overlap ensures no transactions are missed
_HIGH_CONFIDENCE = 0.80      # Threshold for immediate event insertion
_PRIORITY_ALERT = 85.0       # Threshold for WebSocket notification


@app.task(
    name="services.workers.tasks.event_scan.run_event_scan",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=300,
    time_limit=360,
    acks_late=True,
)
def run_event_scan(self):
    """Celery task entry point."""
    try:
        return asyncio.run(_event_scan_async())
    except Exception as exc:
        logger.error("event_scan_failed", error=str(exc))
        raise self.retry(exc=exc)


async def _event_scan_async() -> dict:
    """Async implementation of the 15-minute event scan."""
    start = datetime.now(timezone.utc)
    cutoff = start - timedelta(minutes=_SCAN_WINDOW_MINUTES)

    logger.info("event_scan_started", window_start=cutoff.isoformat())

    db = None
    redis = None
    customers_scanned = 0
    events_detected = 0
    alerts_pushed = 0

    try:
        db, redis = await _get_dependencies()

        # Step 1: Find customers with new transactions
        customer_ids = await _get_customers_with_new_txns(db, cutoff)
        logger.info("customers_with_new_transactions", count=len(customer_ids))

        for customer_id in customer_ids:
            try:
                new_events, rm_id, priority = await _process_customer(
                    customer_id, db, redis
                )
                customers_scanned += 1
                events_detected += new_events

                # Push WebSocket alert if priority crosses threshold
                if new_events > 0 and priority and priority >= _PRIORITY_ALERT:
                    await _push_realtime_notification(rm_id, customer_id, priority, redis)
                    alerts_pushed += 1

            except Exception as exc:
                logger.error("customer_event_scan_failed", customer_id=customer_id, error=str(exc))

        summary = {
            "customers_scanned": customers_scanned,
            "events_detected": events_detected,
            "alerts_pushed": alerts_pushed,
        }
        logger.info("event_scan_complete", **summary)
        return summary

    finally:
        if db:
            await db.close()


async def _process_customer(customer_id: str, db, redis) -> tuple[int, str, float]:
    """
    Run event detection for a single customer.
    Returns (new_events_count, rm_id, max_priority_score).
    """
    from sqlalchemy import select, and_
    from datetime import timedelta
    from shared.db.models import Customer, Transaction, DetectedEvent, Opportunity
    from shared.constants.enums import TransactionDirection, OpportunityStatus

    now = datetime.now(timezone.utc)
    cutoff_90d = now - timedelta(days=90)

    # Fetch recent transactions for event rule engine
    txn_result = await db.execute(
        select(Transaction).where(
            and_(
                Transaction.customer_id == customer_id,
                Transaction.txn_at >= cutoff_90d,
            )
        ).order_by(Transaction.txn_at.desc())
    )
    transactions = txn_result.scalars().all()

    # Run rule engine
    from shared.models.agent_state import TransactionSummary, CategorySpend, CustomerProfile
    from services.orchestrator.agents.event_detection import EventDetectionAgent

    # Minimal state for the rule engine
    txn_summary = _build_minimal_txn_summary(transactions, customer_id)
    state = {
        "customer_id": customer_id,
        "transactions_summary": txn_summary,
        "detected_events": [],
        "errors": [],
    }

    agent = EventDetectionAgent(db=db, redis=redis)
    result = await agent.execute(state)
    detected = result.get("detected_events", [])

    # Get RM ID
    cust_result = await db.execute(
        select(Customer.rm_id).where(Customer.id == customer_id)
    )
    cust_row = cust_result.fetchone()
    rm_id = str(cust_row.rm_id) if cust_row else "unknown"

    new_event_count = 0
    max_priority = 0.0

    for event in detected:
        if event.confidence_score < _HIGH_CONFIDENCE:
            continue

        # Check if this event was already detected
        existing = await db.execute(
            select(DetectedEvent.id).where(
                and_(
                    DetectedEvent.customer_id == customer_id,
                    DetectedEvent.event_type == event.event_type,
                )
            )
        )
        if existing.scalar():
            continue  # Already in DB

        # Insert new high-confidence event
        new_event = DetectedEvent(
            id=uuid.uuid4(),
            customer_id=uuid.UUID(customer_id),
            event_type=event.event_type,
            confidence_score=event.confidence_score,
            signals=event.signals,
        )
        db.add(new_event)
        await db.commit()
        new_event_count += 1
        logger.info(
            "high_confidence_event_detected",
            customer_id=customer_id,
            event_type=event.event_type.value,
            confidence=event.confidence_score,
        )

        # Trigger re-scoring for this customer
        from services.workers.tasks.daily_scoring import _score_customer
        from services.ml.features.feature_store import invalidate_features
        await invalidate_features(customer_id, redis)
        opp_data = await _score_customer(customer_id, db, redis)

        if opp_data:
            max_priority = max(max_priority, opp_data.get("priority_score", 0.0))

            # Invalidate RM priority queue cache
            cache_key = f"priority_queue:{rm_id}"
            if redis:
                await redis.delete(cache_key)

    return new_event_count, rm_id, max_priority


async def _get_customers_with_new_txns(db, cutoff: datetime) -> list[str]:
    """Get distinct customer IDs with transactions since cutoff."""
    from sqlalchemy import select, distinct
    from shared.db.models import Transaction

    result = await db.execute(
        select(distinct(Transaction.customer_id)).where(
            Transaction.txn_at >= cutoff
        )
    )
    return [str(row[0]) for row in result.fetchall()]


def _build_minimal_txn_summary(transactions, customer_id: str) -> "TransactionSummary":
    """Build a minimal TransactionSummary for the event detection agent."""
    from shared.models.agent_state import TransactionSummary, CategorySpend
    from shared.constants.enums import TransactionDirection

    total_debit = sum(float(t.amount) for t in transactions if t.direction == TransactionDirection.DEBIT)
    total_credit = sum(float(t.amount) for t in transactions if t.direction == TransactionDirection.CREDIT)

    # Initialize behavioral signals
    has_jewellery_spend = False
    jewellery_total = 0.0
    has_banquet_spend = False
    banquet_total = 0.0
    has_luxury_spend = False
    luxury_total = 0.0
    has_property_payment = False
    property_total = 0.0
    has_gst_payment = False
    has_vendor_payments = False
    vendor_payment_count = 0
    large_one_time_credit = None
    has_forex_transfer = False
    forex_transfer_total = 0.0

    # Build category spends and extract signals
    mcc_totals: dict[str, float] = {}
    mcc_counts: dict[str, int] = {}
    
    for t in transactions:
        amount = float(t.amount)
        mcc = t.merchant_category or ""
        merchant = (t.merchant_name or "").lower()

        # Check credits for large one-time credit
        if t.direction == TransactionDirection.CREDIT:
            if amount > 100000:
                if large_one_time_credit is None or amount > large_one_time_credit:
                    large_one_time_credit = amount
            continue

        # Debit transaction signals
        mcc_totals[mcc] = mcc_totals.get(mcc, 0.0) + amount
        mcc_counts[mcc] = mcc_counts.get(mcc, 0) + 1

        # Wedding signals: jewellery (5094) or banquet (7922 / 5812 with banquet keywords)
        if mcc == "5094" or "jeweller" in merchant or "tanishq" in merchant:
            has_jewellery_spend = True
            jewellery_total += amount
        if mcc == "7922" or "banquet" in merchant or "palace banquet" in merchant:
            has_banquet_spend = True
            banquet_total += amount

        # Property signals (6552)
        if mcc == "6552" or "properties" in merchant or "dlf" in merchant:
            has_property_payment = True
            property_total += amount

        # GST signals (9311)
        if mcc == "9311" or "gst" in merchant:
            has_gst_payment = True

        # Vendor signals (5065)
        if mcc == "5065" or "vendor" in merchant or "supplier" in merchant:
            has_vendor_payments = True
            vendor_payment_count += 1

        # Forex transfer / wealth migration signals (offshore/wire/international)
        if "international wire" in merchant or "offshore" in merchant:
            has_forex_transfer = True
            forex_transfer_total += amount

    category_spends = [
        CategorySpend(
            mcc_code=mcc,
            category_name=mcc,
            total_amount=amt,
            transaction_count=mcc_counts[mcc],
            avg_transaction=amt / mcc_counts[mcc],
            pct_of_total_spend=round(amt / (total_debit or 1.0) * 100, 2),
        )
        for mcc, amt in mcc_totals.items()
    ]

    return TransactionSummary(
        customer_id=customer_id,
        total_debit_90d=total_debit,
        total_credit_90d=total_credit,
        spend_by_category=category_spends,
        has_jewellery_spend=has_jewellery_spend,
        jewellery_total=jewellery_total,
        has_banquet_spend=has_banquet_spend,
        banquet_total=banquet_total,
        has_property_payment=has_property_payment,
        property_total=property_total,
        has_gst_payment=has_gst_payment,
        has_vendor_payments=has_vendor_payments,
        vendor_payment_count=vendor_payment_count,
        large_one_time_credit=large_one_time_credit,
        has_forex_transfer=has_forex_transfer,
        forex_transfer_total=forex_transfer_total,
    )


async def _push_realtime_notification(rm_id: str, customer_id: str, priority: float, redis) -> None:
    """Publish a real-time notification on the RM's Redis Pub/Sub channel."""
    if redis is None:
        return

    channel = f"rm_notifications:{rm_id}"
    payload = json.dumps({
        "type": "new_opportunity",
        "customer_id": customer_id,
        "priority_score": round(priority, 1),
        "message": f"High-priority opportunity detected (score: {priority:.0f})",
    })

    try:
        await redis.publish(channel, payload)
        logger.info("realtime_notification_pushed", rm_id=rm_id, priority=priority)
    except Exception as exc:
        logger.warning("realtime_notification_failed", error=str(exc))


async def _get_dependencies():
    from shared.db.session import get_async_session
    import redis.asyncio as aioredis
    from shared.config.settings import get_settings

    settings = get_settings()
    redis_client = await aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    async for session in get_async_session():
        return session, redis_client
    return None, redis_client
