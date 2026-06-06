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
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.gateway.middleware.auth import get_current_rm
from shared.db.models import RelationshipManager

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


async def _stream_placeholder_response(
    message: str, trace_id: str, session_id: str
) -> AsyncGenerator[str, None]:
    """
    Placeholder streaming generator for Phase 3.
    Streams a structured response explaining that the RMCopilotAgent will be
    connected in Phase 5. Each 'token' is yielded with a small delay to
    demonstrate the SSE infrastructure is working.

    Phase 5 replaces this with: await rm_copilot_agent.stream(state)
    """
    placeholder_tokens = [
        "RM Copilot is initialising... ",
        "The conversational agent (RMCopilotAgent) ",
        "will be connected in Phase 5. ",
        "Your query has been received: ",
        f'"{message[:80]}{"..." if len(message) > 80 else ""}" ',
        f"Session: {session_id}",
    ]

    for token in placeholder_tokens:
        yield await _sse_event({"token": token, "trace_id": trace_id, "done": False})
        await asyncio.sleep(0.05)  # Simulate streaming latency

    yield await _sse_event({"token": "", "trace_id": trace_id, "done": True})


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
) -> StreamingResponse:
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    session_id = body.session_id or str(uuid.uuid4())

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
            async for chunk in _stream_placeholder_response(
                message=body.message,
                trace_id=trace_id,
                session_id=session_id,
            ):
                # Check if client disconnected mid-stream
                if await request.is_disconnected():
                    logger.info("copilot_stream_client_disconnected", trace_id=trace_id)
                    break
                yield chunk
        except Exception as exc:
            logger.error("copilot_stream_error", trace_id=trace_id, error=str(exc))
            yield await _sse_event(
                {"error": "An error occurred during streaming.", "trace_id": trace_id, "done": True}
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering for SSE
            "X-Trace-ID": trace_id,
        },
    )
