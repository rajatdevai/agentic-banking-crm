import json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from shared.db.base import Base
from shared.db.models import RelationshipManager, Customer, CustomerProfile, Opportunity, DetectedEvent, OutreachCampaign
from shared.constants.enums import OpportunityStatus, ProductType, EventType, OutreachChannel, PersonaType, RiskTier
from services.workers.tasks.report_gen import _generate_morning_reports_async, _build_report_for_rm


@pytest.fixture
def mock_redis():
    """In-memory mock for Redis — stores key-value pairs in a dict."""
    store: dict[str, str] = {}

    client = AsyncMock()
    client.get = AsyncMock(side_effect=lambda key: store.get(key))
    client.setex = AsyncMock(
        side_effect=lambda key, ttl, value: store.update({key: value})
    )
    client.close = AsyncMock()
    client._store = store  # expose for assertions
    return client


@pytest.fixture
async def db_session():
    """
    Async SQLAlchemy session using an in-memory SQLite database.
    Creates all tables, yields session, then tears down.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_generate_morning_reports(db_session, mock_redis):
    # 1. Create Relationship Managers (one active, one inactive)
    active_rm_id = uuid.uuid4()
    inactive_rm_id = uuid.uuid4()

    active_rm = RelationshipManager(
        id=active_rm_id,
        email="active_rm@bank.com",
        hashed_password="hashedpassword123",
        name="Active RM",
        branch_code="B001",
        is_active=True,
    )
    inactive_rm = RelationshipManager(
        id=inactive_rm_id,
        email="inactive_rm@bank.com",
        hashed_password="hashedpassword456",
        name="Inactive RM",
        branch_code="B002",
        is_active=False,
    )
    db_session.add_all([active_rm, inactive_rm])
    await db_session.flush()

    # 2. Create Customers for Active RM
    cust1_id = uuid.uuid4()
    cust2_id = uuid.uuid4()
    cust3_id = uuid.uuid4()
    cust_inactive_id = uuid.uuid4()

    cust1 = Customer(
        id=cust1_id,
        rm_id=active_rm_id,
        persona_type=PersonaType.YOUNG_IT_PROFESSIONAL,
        risk_tier=RiskTier.LOW,
    )
    cust2 = Customer(
        id=cust2_id,
        rm_id=active_rm_id,
        persona_type=PersonaType.DOCTOR,
        risk_tier=RiskTier.LOW,
    )
    cust3 = Customer(
        id=cust3_id,
        rm_id=active_rm_id,
        persona_type=PersonaType.STARTUP_FOUNDER,
        risk_tier=RiskTier.MEDIUM,
    )
    cust_inactive = Customer(
        id=cust_inactive_id,
        rm_id=inactive_rm_id,
        persona_type=PersonaType.YOUNG_IT_PROFESSIONAL,
        risk_tier=RiskTier.LOW,
    )
    db_session.add_all([cust1, cust2, cust3, cust_inactive])
    await db_session.flush()

    # 3. Create Customer Profiles
    prof1 = CustomerProfile(
        customer_id=cust1_id,
        credit_score=750,
        avg_balance_3m=120000.0,
        product_holdings={},
        behavioral_tags=[],
    )
    prof2 = CustomerProfile(
        customer_id=cust2_id,
        credit_score=800,
        avg_balance_3m=500000.0,
        product_holdings={},
        behavioral_tags=[],
    )
    db_session.add_all([prof1, prof2])
    await db_session.flush()

    # 4. Create Opportunities
    # Active RM opportunities
    opp1 = Opportunity(
        id=uuid.uuid4(),
        customer_id=cust1_id,
        product_recommended=ProductType.PERSONAL_LOAN,
        priority_score=92.5,
        conversion_prob=0.85,
        revenue_potential=15000.0,
        explanation="High-value conversion opportunity",
        status=OpportunityStatus.NEW,
    )
    opp2 = Opportunity(
        id=uuid.uuid4(),
        customer_id=cust2_id,
        product_recommended=ProductType.HOME_LOAN,
        priority_score=75.0,
        conversion_prob=0.60,
        revenue_potential=80000.0,
        explanation="IT professional growth opportunity",
        status=OpportunityStatus.NEW,
    )
    opp3_dismissed = Opportunity(
        id=uuid.uuid4(),
        customer_id=cust3_id,
        product_recommended=ProductType.PERSONAL_LOAN,
        priority_score=95.0,
        conversion_prob=0.90,
        revenue_potential=15000.0,
        explanation="Should be ignored as it is dismissed",
        status=OpportunityStatus.DISMISSED,
    )
    # Inactive RM opportunity
    opp_inactive = Opportunity(
        id=uuid.uuid4(),
        customer_id=cust_inactive_id,
        product_recommended=ProductType.PERSONAL_LOAN,
        priority_score=90.0,
        conversion_prob=0.80,
        revenue_potential=15000.0,
        explanation="Should be ignored as RM is inactive",
        status=OpportunityStatus.NEW,
    )
    db_session.add_all([opp1, opp2, opp3_dismissed, opp_inactive])
    await db_session.flush()

    # 5. Create Detected Events (Some overnight, some older, some other RM)
    now = datetime.now(timezone.utc)
    event1 = DetectedEvent(
        id=uuid.uuid4(),
        customer_id=cust1_id,
        event_type=EventType.WEDDING,
        confidence_score=0.85,
        signals={},
        detected_at=now - timedelta(hours=2),
    )
    event2 = DetectedEvent(
        id=uuid.uuid4(),
        customer_id=cust2_id,
        event_type=EventType.HOME_PURCHASE,
        confidence_score=0.90,
        signals={},
        detected_at=now - timedelta(hours=10),
    )
    event3_old = DetectedEvent(
        id=uuid.uuid4(),
        customer_id=cust1_id,
        event_type=EventType.PROMOTION,
        confidence_score=0.95,
        signals={},
        detected_at=now - timedelta(hours=15),  # older than 12h
    )
    event_inactive = DetectedEvent(
        id=uuid.uuid4(),
        customer_id=cust_inactive_id,
        event_type=EventType.WEDDING,
        confidence_score=0.80,
        signals={},
        detected_at=now - timedelta(hours=3),  # inactive RM customer
    )
    db_session.add_all([event1, event2, event3_old, event_inactive])
    await db_session.flush()

    # 6. Create Outreach Campaigns (yesterday calendar day UTC)
    yesterday = now.date() - timedelta(days=1)
    yesterday_midday = datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
    two_days_ago_midday = yesterday_midday - timedelta(days=1)

    campaign1 = OutreachCampaign(
        id=uuid.uuid4(),
        opportunity_id=opp1.id,
        channel=OutreachChannel.WHATSAPP,
        message_body="Test msg 1",
        sent_at=yesterday_midday,
        delivered_at=yesterday_midday + timedelta(minutes=5),
        opened_at=yesterday_midday + timedelta(minutes=10),
        converted_at=yesterday_midday + timedelta(hours=1),
    )
    campaign2 = OutreachCampaign(
        id=uuid.uuid4(),
        opportunity_id=opp2.id,
        channel=OutreachChannel.WHATSAPP,
        message_body="Test msg 2",
        sent_at=yesterday_midday,
        delivered_at=yesterday_midday + timedelta(minutes=2),
    )
    campaign_old = OutreachCampaign(
        id=uuid.uuid4(),
        opportunity_id=opp1.id,
        channel=OutreachChannel.WHATSAPP,
        message_body="Test msg old",
        sent_at=two_days_ago_midday,
    )
    campaign_inactive = OutreachCampaign(
        id=uuid.uuid4(),
        opportunity_id=opp_inactive.id,
        channel=OutreachChannel.WHATSAPP,
        message_body="Test msg inactive",
        sent_at=yesterday_midday,
    )
    db_session.add_all([campaign1, campaign2, campaign_old, campaign_inactive])
    await db_session.commit()

    # Patch dependencies and run report generation
    with patch("services.workers.tasks.report_gen._get_dependencies", return_value=(db_session, mock_redis)):
        summary = await _generate_morning_reports_async()

    assert summary["rms_processed"] == 1
    assert summary["rms_failed"] == 0

    # Verify report stored in redis under correct key prefix
    cache_key = f"morning_digest:{active_rm_id}"
    assert cache_key in mock_redis._store

    report = json.loads(mock_redis._store[cache_key])
    assert report["rm_id"] == str(active_rm_id)
    assert report["overnight_events_count"] == 2

    # Check top priority customers
    top_custs = report["top_customers"]
    assert len(top_custs) == 2
    # Sorted by priority score descending: opp1 (92.5) first, then opp2 (75.0)
    assert top_custs[0]["customer_id"] == str(cust1_id)
    assert top_custs[0]["priority_score"] == 92.5
    assert top_custs[0]["explanation"] == "High-value conversion opportunity"
    assert top_custs[0]["credit_score"] == 750

    assert top_custs[1]["customer_id"] == str(cust2_id)
    assert top_custs[1]["priority_score"] == 75.0
    assert top_custs[1]["explanation"] == "IT professional growth opportunity"
    assert top_custs[1]["credit_score"] == 800

    # Check outreach statistics for yesterday
    outreach_stats = report["yesterday_outreach_stats"]
    assert outreach_stats["sent"] == 2
    assert outreach_stats["delivered"] == 2
    assert outreach_stats["opened"] == 1
    assert outreach_stats["converted"] == 1
