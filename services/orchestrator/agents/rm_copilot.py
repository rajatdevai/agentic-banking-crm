"""
RMCopilotAgent — RAG-backed conversational assistant (gpt-4o, streaming).

Reads: rm_question, rm_id from state
Writes: copilot_response_chunks (list[str] — tokens accumulated for SSE streaming)

Flow:
    1. Retrieve context from all four RAG collections in parallel
    2. Fetch RM's portfolio summary from DB (customer count, top personas, recent events)
    3. Construct masked prompt via Jinja2 prompt registry
    4. Stream response token-by-token back through copilot_response_chunks
       (gateway SSE endpoint consumes these chunks)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import structlog
from sqlalchemy import func, select

from services.orchestrator.agents.base_agent import BaseAgent
from services.orchestrator.graph.state import AgentState
from services.orchestrator.llm.prompt_registry import PromptKey, render_prompt
from services.orchestrator.llm.router import get_llm_router
from shared.db.models import Customer, DetectedEvent as DetectedEventORM, Opportunity

logger = structlog.get_logger(__name__)

_RAG_COLLECTIONS = ["product_catalog", "policy_docs", "persona_playbooks", "market_context"]


class RMCopilotAgent(BaseAgent):
    agent_name = "RMCopilotAgent"
    timeout_seconds = 90.0   # Streaming can be longer

    async def execute(self, state: AgentState) -> dict:
        rm_question: Optional[str] = state.get("rm_question")
        rm_id: str = state.get("rm_id", "")
        session_id: str = state.get("session_id", "unknown")

        if not rm_question:
            return {"copilot_response_chunks": ["[No question provided]"]}

        # 1. Retrieve relevant context from all RAG collections
        rag_context = await self._retrieve_rag_context(rm_question)

        # 2. Fetch portfolio summary for context
        portfolio_summary = await self._get_portfolio_summary(rm_id)

        # 3. Build masked prompt
        prompt = render_prompt(
            PromptKey.RM_COPILOT_CONVERSATION,
            rm_name="Relationship Manager",
            rm_question=rm_question,
            portfolio_summary=portfolio_summary,
            rag_context=rag_context,
            current_date=date.today().isoformat(),
        )

        self.assert_no_pii_in_prompt(prompt)

        # 4. Stream response
        chunks: list[str] = []
        try:
            async for token, is_final in get_llm_router().stream_primary(
                prompt=prompt,
                session_id=session_id,
            ):
                if token:
                    chunks.append(token)
                if is_final:
                    break
        except Exception as exc:
            logger.error("copilot_stream_error", error=str(exc), session_id=session_id)
            chunks = [f"I encountered an error processing your question. Please try again. ({type(exc).__name__})"]

        logger.info(
            "copilot_response_complete",
            session_id=session_id,
            response_chunks=len(chunks),
        )

        return {"copilot_response_chunks": chunks}

    async def _retrieve_rag_context(self, question: str) -> str:
        """Retrieve relevant context from all four RAG collections in parallel."""
        import asyncio

        async def _fetch_one(collection: str) -> str:
            try:
                from services.orchestrator.tools.vector_tools import hybrid_search
                results = await hybrid_search(
                    query=question,
                    collection=collection,
                    top_k=2,
                    db=self._db,
                    redis_client=self._redis,
                )
                return "\n".join(r.get("content", "")[:300] for r in results)
            except Exception as exc:
                logger.warning("copilot_rag_failed", collection=collection, error=str(exc))
                return ""

        results = await asyncio.gather(*[_fetch_one(col) for col in _RAG_COLLECTIONS])
        combined = "\n\n".join(
            f"[{col.upper()}]\n{text}"
            for col, text in zip(_RAG_COLLECTIONS, results)
            if text.strip()
        )
        return combined or "No relevant context found in knowledge base."

    async def _get_portfolio_summary(self, rm_id: str) -> str:
        """Fetch a brief portfolio summary for the RM from the database."""
        if not self._db or not rm_id:
            return "Portfolio summary unavailable."

        try:
            # Customer count
            count_result = await self._db.execute(
                select(func.count(Customer.id)).where(
                    Customer.rm_id == rm_id,
                    Customer.deleted_at.is_(None),
                )
            )
            total_customers = count_result.scalar() or 0

            # Open opportunities count
            opp_result = await self._db.execute(
                select(func.count(Opportunity.id)).where(
                    Opportunity.deleted_at.is_(None),
                )
            )
            open_opps = opp_result.scalar() or 0

            return (
                f"Portfolio: {total_customers} active customers, "
                f"{open_opps} open opportunities."
            )
        except Exception as exc:
            logger.warning("portfolio_summary_failed", error=str(exc))
            return "Portfolio summary temporarily unavailable."
