import json
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from services.orchestrator.agents.rm_copilot import RMCopilotAgent
from services.orchestrator.graph.state import AgentState
from shared.constants.enums import PersonaType, EventType, ProductType

@pytest.fixture
def mock_llm_parser():
    mock_router = AsyncMock()
    # Mock LLM response to return parsed filters
    mock_router.call_primary = AsyncMock(return_value=json.dumps({
        "persona_type": "hni",
        "event_type": "wedding",
        "product_type": "personal_loan",
        "time_window": "this month",
        "is_pipeline_query": True
    }))
    
    with patch("services.orchestrator.agents.rm_copilot.get_llm_router", return_value=mock_router):
        yield mock_router

@pytest.fixture
async def db_session():
    """In-memory SQLite database for unit testing."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from shared.db.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    await engine.dispose()

@pytest.mark.asyncio
async def test_rm_copilot_parses_filters(db_session, mock_llm_parser):
    agent = RMCopilotAgent(db=db_session, redis=None)
    
    # Run agent execution
    state = {
        "rm_question": "Find HNI with wedding events this month",
        "rm_id": str(uuid.uuid4()),
        "session_id": "test-session-123",
        "token_queue": AsyncMock()
    }
    
    # We patch build_scoring_graph and database queries since we only want to test the parsing and filtering logic in RMCopilotAgent
    with patch("services.orchestrator.graph.builder.build_scoring_graph") as mock_graph_builder:
        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={
            "opportunities": [],
            "risk_assessment": None,
            "errors": [],
            "agent_trace": []
        })
        mock_graph_builder.return_value = mock_graph
        
        # Mocking db execute to return empty list of customers to avoid database constraint errors
        with patch.object(db_session, "execute") as mock_execute:
            mock_result = AsyncMock()
            mock_result.scalars = lambda: AsyncMock(all=lambda: [])
            mock_execute.return_value = mock_result
            
            await agent.execute(state)
            
            # Assertions
            mock_llm_parser.call_primary.assert_called_once()
            
            # Verify the prompt rendered with the RM's question
            called_kwargs = mock_llm_parser.call_primary.call_args[1]
            assert "Find HNI with wedding events this month" in called_kwargs["prompt"]
