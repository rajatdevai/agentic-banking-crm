"""
Copilot chat endpoint — streaming Server-Sent Events (SSE).

The RM asks a free-text question. The response is streamed token-by-token
using SSE so the RM sees the answer appear in real time, just like ChatGPT.

SSE event format per token:
    data: {"token": "...", "trace_id": "...", "done": false}\n\n

Final event when stream completes:
    data: {"token": "", "trace_id": "...", "done": true}\n\n

Error during streaming:
    data: {"error": "...", "trace_id": "...", "done": true}\n\n

Note: The actual LangGraph RMCopilotAgent streaming is implemented in Phase 5.
This router sets up the SSE infrastructure and returns a structured placeholder
so the frontend can be built against a stable contract immediately.
"""

import asyncio
import json
import uuid
from datetime import date
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from services.gateway.middleware.auth import get_current_rm
from shared.db.models import RelationshipManager
from shared.db.session import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/copilot", tags=["Copilot Chat"])


class ChatRequest(BaseModel):
    """Request body for the copilot chat endpoint."""

    message: str = Field(
        ...,
        description="The RM's free-text question or instruction",
        min_length=1,
        max_length=4096,
    )
    session_id: str | None = Field(
        None,
        description=(
            "Session ID for conversation continuity. If None, a new session is started. "
            "Pass the same session_id across turns to maintain conversation history."
        ),
    )
    customer_context_ids: list[str] = Field(
        default_factory=list,
        description="Optional list of customer UUIDs to scope the copilot's context",
    )


async def _sse_event(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"


@router.post(
    "/chat",
    summary="Conversational copilot — streams response via SSE",
    description=(
        "Accepts a free-text RM question and streams the RMCopilotAgent response "
        "token-by-token via Server-Sent Events. Maintain session_id across turns "
        "for conversation continuity. "
        "Rate limit: 10 requests per minute (LLM-heavy category)."
    ),
    response_class=StreamingResponse,
)
async def copilot_chat(
    request: Request,
    body: ChatRequest,
    current_rm: RelationshipManager = Depends(get_current_rm),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    session_id = body.session_id or str(uuid.uuid4())
    redis = request.app.state.redis

    logger.info(
        "copilot_chat_request",
        rm_id=str(current_rm.id),
        trace_id=trace_id,
        session_id=session_id,
        message_length=len(body.message),
        customer_context_count=len(body.customer_context_ids),
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            from services.orchestrator.agents.rm_copilot import RMCopilotAgent
            from services.orchestrator.tools.vector_tools import search_knowledge_base
            from services.orchestrator.llm.prompt_registry import render_prompt, PromptKey
            from services.orchestrator.llm.router import get_llm_router

            # Instantiate agent
            agent = RMCopilotAgent(db=db, redis=redis)

            # Retrieve context with citations — all 4 collections fetched IN PARALLEL
            citations = []
            rag_context_parts = []
            collections = ["product_catalog", "policy_docs", "persona_playbooks", "market_context"]

            rag_results = await asyncio.gather(
                *[
                    search_knowledge_base(
                        query=body.message,
                        db=db,
                        doc_type_filter=col,
                        redis_client=redis,
                        top_k=2,
                    )
                    for col in collections
                ],
                return_exceptions=True,
            )

            for col, context_res in zip(collections, rag_results):
                if isinstance(context_res, Exception):
                    logger.warning("rag_collection_error", collection=col, error=str(context_res))
                    continue
                if context_res.formatted_context and context_res.formatted_context != "No relevant context found in the knowledge base.":
                    rag_context_parts.append(f"[{col.upper()}]\n{context_res.formatted_context}")
                    for cit in context_res.source_citations:
                        citations.append({
                            "source": cit.get("source_file", ""),
                            "doc_type": cit.get("doc_type", ""),
                            "excerpt": cit.get("excerpt", "")[:250],
                            "score": cit.get("rrf_score", 0.0),
                        })

            rag_context = "\n\n".join(rag_context_parts) or "No relevant context found in knowledge base."

            # Fetch portfolio summary
            portfolio_summary = await agent._get_portfolio_summary(str(current_rm.id))

            # Build masked prompt
            prompt = render_prompt(
                PromptKey.RM_COPILOT_CONVERSATION,
                rm_name=current_rm.name or "Relationship Manager",
                rm_question=body.message,
                portfolio_summary=portfolio_summary,
                rag_context=rag_context,
                current_date=date.today().isoformat(),
            )

            # PII Pre-flight guard
            agent.assert_no_pii_in_prompt(prompt)

            # Stream response token-by-token
            async for token, is_final in get_llm_router().stream_primary(
                prompt=prompt,
                session_id=session_id,
            ):
                if await request.is_disconnected():
                    logger.info("copilot_stream_client_disconnected", trace_id=trace_id)
                    break
                if is_final:
                    # Final event with citations
                    yield await _sse_event({
                        "token": "",
                        "trace_id": trace_id,
                        "done": True,
                        "citations": citations
                    })
                else:
                    yield await _sse_event({
                        "token": token,
                        "trace_id": trace_id,
                        "done": False
                    })

        except Exception as exc:
            logger.error("copilot_stream_error", trace_id=trace_id, error=str(exc))
            yield await _sse_event({
                "error": f"An error occurred: {type(exc).__name__}: {str(exc)}",
                "trace_id": trace_id,
                "done": True
            })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering for SSE
            "X-Trace-ID": trace_id,
        },
    )
