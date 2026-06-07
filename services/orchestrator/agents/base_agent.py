"""
BaseAgent — abstract contract every agent in the RM Copilot platform must implement.

Every agent subclass must implement exactly ONE method:
    async def execute(self, state: AgentState) -> dict

The BaseAgent wrapper provides:
    1. Tenacity retry — 3 attempts, exponential backoff, transient-error-only retry
    2. Per-agent configurable timeout (asyncio.wait_for)
    3. PII safety pre-flight — scans any string being sent to LLM for raw PII tokens
    4. Automatic audit logging to agent_execution_logs (append-only, non-blocking)
    5. Error capture — appends to state.errors and state.agent_trace instead of crashing

Usage:
    class CustomerIntelAgent(BaseAgent):
        agent_name = "CustomerIntelAgent"
        timeout_seconds = 10

        async def execute(self, state: AgentState) -> dict:
            ...
            return {"customer_profile": profile}

    # In graph builder:
    agent = CustomerIntelAgent(db=session, settings=settings)
    result = await agent.run(state)   # run() = BaseAgent wrapper around execute()
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional

import structlog
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from services.orchestrator.graph.state import AgentState
from shared.config.settings import get_settings
from shared.exceptions.domain import (
    AgentExecutionError,
    LLMUnavailableError,
    PIIDetectedInOutputError,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for PII pre-flight check (fast, no Presidio overhead)
# These are conservative — false positives are acceptable here.
# ---------------------------------------------------------------------------
_PII_PATTERNS = [
    re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),                # PAN card
    re.compile(r"\+91[\s\-]?[6-9][0-9]{9}\b"),                # Indian mobile (intl)
    re.compile(r"\b[6-9][0-9]{9}\b"),                          # Indian mobile (local)
    re.compile(r"\b[2-9][0-9]{3}\s?[0-9]{4}\s?[0-9]{4}\b"),   # Aadhaar
    re.compile(r"\b[0-9]{9,18}\b"),                            # Account number (broad)
]

# Transient errors that justify a retry
_RETRYABLE_EXCEPTIONS = (
    LLMUnavailableError,
    TimeoutError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


def _contains_pii(text: str) -> bool:
    """Quick regex scan for obvious PII patterns. Used as a pre-flight guard."""
    for pattern in _PII_PATTERNS:
        if pattern.search(text):
            return True
    return False


class BaseAgent(ABC):
    """
    Abstract base for all RM Copilot agents.

    Subclasses must define:
        agent_name: str          — unique name used in logs and agent_trace
        timeout_seconds: float   — per-execution timeout (default 30s)

    Subclasses must implement:
        async execute(state) -> dict
    """

    agent_name: str = "BaseAgent"
    timeout_seconds: float = 30.0

    def __init__(
        self,
        db=None,           # AsyncSession — injected for DB-dependent agents
        redis=None,        # Redis client — injected for cache-dependent agents
        settings=None,     # Settings — resolved lazily if not injected
    ):
        self._db = db
        self._redis = redis
        self._settings = settings or get_settings()

    @abstractmethod
    async def execute(self, state: AgentState) -> dict:
        """
        Core agent logic. Must return a dict containing ONLY the state fields
        this agent writes. The graph merges this dict into the running state.

        Do NOT mutate state directly. Return the fields you change.
        """
        ...

    async def run(self, state: AgentState) -> dict:
        """
        Public entry point. Wraps execute() with retry, timeout, PII guard,
        audit logging, and error capture.

        Always returns a dict safe to merge into state. Never raises exceptions
        (all errors are captured into the errors list).
        """
        start_time = time.monotonic()
        error_message: Optional[str] = None
        result: dict = {}

        try:
            result = await self._run_with_retry(state)
        except PIIDetectedInOutputError as exc:
            error_message = f"[PII_SAFETY] {self.agent_name}: {exc}"
            logger.critical(
                "pii_safety_violation",
                agent=self.agent_name,
                session_id=state.get("session_id"),
            )
        except AgentExecutionError as exc:
            error_message = f"[AGENT_ERROR] {self.agent_name}: {exc.reason}"
            logger.error("agent_execution_failed", agent=self.agent_name, reason=exc.reason)
        except RetryError as exc:
            error_message = f"[RETRY_EXHAUSTED] {self.agent_name}: {exc}"
            logger.error("agent_retry_exhausted", agent=self.agent_name, error=str(exc))
        except Exception as exc:
            error_message = f"[UNEXPECTED] {self.agent_name}: {type(exc).__name__}: {exc}"
            logger.exception("agent_unexpected_error", agent=self.agent_name)

        latency_ms = int((time.monotonic() - start_time) * 1000)

        # Always append to agent_trace
        partial: dict = {"agent_trace": [self.agent_name]}

        # Capture errors
        if error_message:
            partial["errors"] = [error_message]
        else:
            partial.update(result)

        # Best-effort audit log (awaited to prevent transaction corruption)
        await self._write_audit_log(
            state=state,
            result=result,
            latency_ms=latency_ms,
            error=error_message,
        )

        return partial

    async def _run_with_retry(self, state: AgentState) -> dict:
        """Runs execute() with tenacity retry and asyncio timeout."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            reraise=True,
        ):
            with attempt:
                try:
                    result = await asyncio.wait_for(
                        self.execute(state),
                        timeout=self.timeout_seconds,
                    )
                    return result
                except asyncio.TimeoutError:
                    logger.warning(
                        "agent_timeout",
                        agent=self.agent_name,
                        timeout_s=self.timeout_seconds,
                        attempt_number=attempt.retry_state.attempt_number,
                    )
                    raise

        raise AgentExecutionError(self.agent_name, "All retries exhausted")

    def assert_no_pii_in_prompt(self, prompt: str) -> None:
        """
        Pre-flight PII guard for any string about to be sent to an LLM.
        Raises PIIDetectedInOutputError if raw PII patterns are found.

        Note: This is a defence-in-depth check. Primary masking is done by
        PIIMasker. This catches any agent that accidentally constructs a
        prompt with unmasked data.
        """
        if not get_settings().PII_MASK_ENABLED:
            return
        if _contains_pii(prompt):
            raise PIIDetectedInOutputError(
                agent_name=self.agent_name,
                pii_entities=["DETECTED_BY_REGEX_PREFLIGHT"],
            )

    async def _write_audit_log(
        self,
        state: AgentState,
        result: dict,
        latency_ms: int,
        error: Optional[str],
    ) -> None:
        """
        Append an audit record to agent_execution_logs.
        Non-blocking, best-effort — failures are logged but never re-raised.
        """
        if self._db is None:
            return

        try:
            from shared.db.models import AgentExecutionLog

            session_id_str = state.get("session_id", str(uuid.uuid4()))
            try:
                session_uuid = uuid.UUID(session_id_str)
            except ValueError:
                session_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, session_id_str)

            # Sanitise result — remove any non-serialisable objects
            serialisable_result: dict = {}
            for k, v in result.items():
                try:
                    import json
                    # Ensure the value contains only JSON-serializable primitives
                    serialisable_result[k] = json.loads(json.dumps(v, default=str))
                except Exception:
                    serialisable_result[k] = str(v)[:200]

            log_entry = AgentExecutionLog(
                id=uuid.uuid4(),
                session_id=session_uuid,
                agent_name=self.agent_name,
                input_masked={
                    "customer_id": state.get("customer_id"),
                    "rm_id": state.get("rm_id"),
                    "trace_id": state.get("trace_id"),
                    # Never log raw customer data — only IDs
                },
                output=serialisable_result,
                latency_ms=latency_ms,
                error=error,
            )
            from sqlalchemy.ext.asyncio import AsyncSession
            async with AsyncSession(self._db.bind, expire_on_commit=False) as audit_db:
                audit_db.add(log_entry)
                await audit_db.commit()

        except Exception as audit_exc:
            logger.error(
                "agent_audit_log_failed",
                agent=self.agent_name,
                error=str(audit_exc),
            )
