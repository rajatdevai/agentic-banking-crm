"""
Integration test: Full scoring graph with wedding customer.

Tests:
    1. CustomerIntelAgent loads profile correctly
    2. TransactionIntelAgent detects jewellery and banquet signals
    3. EventDetectionAgent fires WEDDING event
    4. OpportunityScoringAgent creates PERSONAL_LOAN opportunity
    5. ExplainabilityAgent generates non-empty explanation (LLM mocked)

OpenAI is mocked via pytest-mock / unittest.mock so the test does NOT
make real API calls. The mock returns a valid ExplainabilityOutput JSON.

Requires:
    - A running Postgres instance pointed to by DATABASE_URL env var
    - pip install pytest pytest-asyncio aiosqlite (for test DB)
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.orchestrator.graph.builder import build_scoring_graph
from services.orchestrator.graph.state import initial_state
from shared.constants.enums import EventType, ProductType


# ---------------------------------------------------------------------------
# Mock OpenAI response
# ---------------------------------------------------------------------------
_MOCK_EXPLAINABILITY_RESPONSE = json.dumps({
    "why_selected": "This customer has shown significant wedding-related spending including jewellery and banquet bookings, indicating an upcoming marriage event within 90 days.",
    "event_explanation": "A wedding life event was detected with 85% confidence based on jewellery purchases at Tanishq and a banquet hall booking, both within the last 45 days.",
    "product_rationale": "A personal loan would help this customer manage the lump-sum wedding expenses spread over a comfortable repayment period, reducing financial stress during this milestone.",
    "conversion_reasoning": "With a CIBIL score of 755, stable salary, and existing relationship of 24 months, the conversion probability is high at 72%.",
    "rm_action": "Contact the customer within the next 7 days before wedding expenses peak. A 5-10 lakh personal loan at the current preferential rate would be the recommended offer."
})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_openai_call():
    """Mock the AsyncOpenAI completions call to return explainability JSON."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = _MOCK_EXPLAINABILITY_RESPONSE
    mock_response.usage = MagicMock()
    mock_response.usage.total_tokens = 350

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch(
        "services.orchestrator.llm.router.AsyncOpenAI",
        return_value=mock_client
    ):
        # Also reset the LRU cache so the new mock client is used
        from services.orchestrator.llm import router as llm_router_module
        llm_router_module.get_llm_router.cache_clear()
        yield mock_client
        llm_router_module.get_llm_router.cache_clear()


@pytest.fixture
async def db_session():
    """
    Async SQLAlchemy session using an in-memory SQLite database.
    Creates all tables, yields session, then tears down.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from shared.db.base import Base

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


# ---------------------------------------------------------------------------
# Test: Full pipeline with WEDDING_CUSTOMER
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_wedding_customer(db_session, mock_openai_call):
    """
    End-to-end graph run with a wedding customer.

    Asserts:
        - detected_events contains a WEDDING event
        - opportunities contains a PERSONAL_LOAN opportunity
        - explanation is a non-empty string
        - No errors in state.errors
    """
    from tests.fixtures.synthetic_customers import WEDDING_CUSTOMER, seed_to_db
    from langgraph.checkpoint.memory import MemorySaver

    # Seed test data into SQLite
    synthetic = await seed_to_db(db_session, WEDDING_CUSTOMER)

    # Build graph with in-memory checkpointer (no Redis in tests)
    checkpointer = MemorySaver()
    graph = build_scoring_graph(
        db=db_session,
        redis=None,
        checkpointer=checkpointer,
    )

    # Run graph
    state = initial_state(
        customer_id=synthetic.customer_id,
        customer_name="Test Customer",
        rm_id=synthetic.rm_id,
        rm_name="Test RM",
        session_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
    )

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = await graph.ainvoke(state, config=config)

    # ---- Assertions ----

    # 1. No catastrophic errors (warnings from RAG stubs are OK)
    critical_errors = [
        e for e in (result.get("errors") or [])
        if "RAG" not in e and "xgboost" not in e.lower() and "model" not in e.lower()
    ]
    assert not critical_errors, f"Unexpected errors: {critical_errors}"

    # 2. Wedding event detected
    detected_events = result.get("detected_events") or []
    event_types = [e.event_type for e in detected_events]
    assert EventType.WEDDING in event_types, (
        f"Expected WEDDING event. Got: {[e.value for e in event_types]}"
    )

    # 3. Wedding event has audit signals
    wedding_event = next(e for e in detected_events if e.event_type == EventType.WEDDING)
    assert wedding_event.confidence_score >= 0.40, (
        f"Wedding confidence too low: {wedding_event.confidence_score}"
    )
    assert "jewellery_total" in wedding_event.signals or "banquet_total" in wedding_event.signals, (
        f"Wedding event missing audit signals: {wedding_event.signals}"
    )

    # 4. Personal loan opportunity created
    opportunities = result.get("opportunities") or []
    assert len(opportunities) > 0, "No opportunities generated"
    product_types = [o.product_recommended for o in opportunities]
    assert ProductType.PERSONAL_LOAN in product_types, (
        f"Expected PERSONAL_LOAN opportunity. Got: {[p.value for p in product_types]}"
    )

    # 5. Explanation is non-empty
    explanation = result.get("explanation")
    assert explanation and len(explanation) > 50, (
        f"Explanation is empty or too short: {explanation!r}"
    )

    # 6. Agent trace shows correct sequence
    agent_trace = result.get("agent_trace") or []
    expected_agents = ["CustomerIntelAgent", "TransactionIntelAgent", "EventDetectionAgent"]
    for expected in expected_agents:
        assert expected in agent_trace, (
            f"Expected {expected} in agent_trace. Got: {agent_trace}"
        )


@pytest.mark.asyncio
async def test_no_events_customer_skips_llm(db_session):
    """
    A customer with no transaction signals should hit no_opportunity_node
    and should_skip_llm should be True.
    """
    from tests.fixtures.synthetic_customers import SyntheticCustomer, seed_to_db
    from langgraph.checkpoint.memory import MemorySaver

    # Minimal customer with no event-triggering transactions
    minimal = SyntheticCustomer(
        persona_type="young_it_professional",
        risk_tier="low",
        credit_score=720,
        transactions=[],  # No transactions at all
    )
    await seed_to_db(db_session, minimal)

    checkpointer = MemorySaver()
    graph = build_scoring_graph(db=db_session, redis=None, checkpointer=checkpointer)

    state = initial_state(
        customer_id=minimal.customer_id,
        customer_name="Test Customer",
        rm_id=minimal.rm_id,
        rm_name="Test RM",
        session_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
    )

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = await graph.ainvoke(state, config=config)

    # should_skip_llm should be True
    assert result.get("should_skip_llm") is True, "Expected LLM skip for customer with no events"

    # Explanation should still be populated (from no_opportunity_node)
    explanation = result.get("explanation")
    assert explanation and len(explanation) > 20, "Expected fallback explanation"

    # No outreach messages generated
    outreach = result.get("outreach_messages") or []
    assert len(outreach) == 0, "No outreach should be generated for no-event customer"
