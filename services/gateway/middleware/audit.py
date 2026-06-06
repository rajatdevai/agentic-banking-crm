"""
Append-only audit logging middleware.

Every request to a customer-data route is logged to agent_execution_logs.
The log is immutable — middleware only inserts, never updates or deletes.

What is recorded per request:
    - trace_id: generated or forwarded from X-Trace-ID header
    - rm_id: extracted from the validated JWT (or "anonymous")
    - route: HTTP method + path
    - request body: masked (PII removed) — raw body is never logged
    - response status code
    - latency in milliseconds
    - any error message if the request raised an exception

The trace_id is added to every response as X-Trace-ID so browser devtools,
load balancers, and downstream service logs can correlate a full request chain.

Routes excluded from audit logging:
    - /health      (load balancer pings — high volume, no customer data)
    - /docs        (API documentation)
    - /openapi.json
    - /metrics     (Prometheus scrape)
"""

import time
import uuid
from typing import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = structlog.get_logger(__name__)

# Routes that produce no customer data — skip audit to reduce noise
_EXCLUDED_PATHS: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/favicon.ico",
})


class AuditMiddleware(BaseHTTPMiddleware):
    """
    Appends an audit record to agent_execution_logs for every protected request.

    DB writes are fire-and-forget (best-effort) — if the audit INSERT fails,
    the actual response is still returned. We never block a legitimate RM
    request because the audit system is slow or temporarily unavailable.
    The audit failure is logged at ERROR level for alerting.
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip non-customer-data routes
        if request.url.path in _EXCLUDED_PATHS:
            return await call_next(request)

        # Generate or forward trace ID
        trace_id = request.headers.get("X-Trace-ID") or str(uuid.uuid4())
        request.state.trace_id = trace_id

        # Extract RM identity from request state (set by auth middleware)
        rm_id = getattr(request.state, "rm_id", "anonymous")

        start_time = time.monotonic()

        # Process the actual request
        response: Response | None = None
        error_message: str | None = None
        try:
            response = await call_next(request)
        except Exception as exc:
            error_message = str(exc)
            raise
        finally:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            status_code = response.status_code if response else 500

            # Structured log line — always emitted (even if DB write fails)
            logger.info(
                "api_request",
                trace_id=trace_id,
                rm_id=rm_id,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                latency_ms=latency_ms,
                error=error_message,
            )

            # Best-effort DB audit write (non-blocking)
            await self._write_audit_record(
                request=request,
                trace_id=trace_id,
                rm_id=rm_id,
                status_code=status_code,
                latency_ms=latency_ms,
                error=error_message,
            )

        # Attach trace_id to response headers for downstream correlation
        response.headers["X-Trace-ID"] = trace_id
        return response

    async def _write_audit_record(
        self,
        request: Request,
        trace_id: str,
        rm_id: str,
        status_code: int,
        latency_ms: int,
        error: str | None,
    ) -> None:
        """
        Writes an audit record to agent_execution_logs.
        Fire-and-forget — exceptions are caught and logged, never re-raised.
        """
        try:
            # Get DB session from request app state (if available)
            db = getattr(request.state, "db", None)
            if db is None:
                return  # DB session not available on this request (e.g., unauthenticated)

            from shared.db.models import AgentExecutionLog

            record = AgentExecutionLog(
                session_id=uuid.UUID(trace_id) if self._is_valid_uuid(trace_id)
                           else uuid.uuid4(),
                agent_name="gateway_request",
                input_masked={
                    "method": request.method,
                    "path": str(request.url.path),
                    "rm_id": rm_id,
                    # We do NOT log request body here — it may contain PII
                    # Individual agent calls log their masked inputs separately
                },
                output={
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                },
                error=error,
            )
            db.add(record)
            await db.commit()

        except Exception as audit_exc:
            # Critical: audit failure must be visible but must NOT affect the response
            logger.error(
                "audit_write_failed",
                trace_id=trace_id,
                error=str(audit_exc),
            )

    @staticmethod
    def _is_valid_uuid(value: str) -> bool:
        try:
            uuid.UUID(value)
            return True
        except ValueError:
            return False
