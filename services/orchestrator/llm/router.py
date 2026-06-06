"""
LLM Router — single AsyncOpenAI client for the entire platform.

Two tiers of calls:
    call_primary()  → gpt-4o    (quality tasks: explanation, outreach)
    call_fast()     → gpt-4o-mini (cheaper tasks: summarization, classification)

Both methods:
    - Accept an optional response_model (Pydantic class) for structured output
    - Use JSON mode when a response_model is provided
    - Raise LLMUnavailableError after max retries instead of propagating OpenAI errors
    - Log provider, model, token count, and latency on every call

The router is a singleton — import get_llm_router() to get the shared instance.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Optional, Type, TypeVar

import structlog
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, RateLimitError
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from shared.config.settings import get_settings
from shared.exceptions.domain import LLMUnavailableError

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_RETRYABLE_OPENAI_ERRORS = (
    APIConnectionError,
    RateLimitError,
    TimeoutError,
)


class LLMRouter:
    """
    Async OpenAI client wrapper with retry, structured output, and logging.
    """

    def __init__(self):
        _s = get_settings()
        self._client = AsyncOpenAI(
            api_key=_s.OPENAI_API_KEY,
            timeout=_s.OPENAI_TIMEOUT_SECONDS,
            max_retries=0,  # We handle retries via tenacity for richer control
        )
        self._primary_model = _s.OPENAI_PRIMARY_MODEL
        self._fast_model = _s.OPENAI_FAST_MODEL
        self._max_retries = _s.OPENAI_MAX_RETRIES

    async def call_primary(
        self,
        prompt: str,
        system: str = "You are an expert banking relationship manager AI assistant.",
        response_model: Optional[Type[T]] = None,
        temperature: float = 0.3,
        session_id: str = "unknown",
    ) -> str | T:
        """
        Call gpt-4o for quality-critical tasks (explainability, outreach generation).
        Use for tasks where output quality directly affects RM confidence.
        """
        return await self._call(
            model=self._primary_model,
            prompt=prompt,
            system=system,
            response_model=response_model,
            temperature=temperature,
            session_id=session_id,
        )

    async def call_fast(
        self,
        prompt: str,
        system: str = "You are a helpful banking data analyst.",
        response_model: Optional[Type[T]] = None,
        temperature: float = 0.1,
        session_id: str = "unknown",
    ) -> str | T:
        """
        Call gpt-4o-mini for cost-optimised tasks (summarization, JSON extraction).
        Use when speed and cost matter more than nuanced prose quality.
        """
        return await self._call(
            model=self._fast_model,
            prompt=prompt,
            system=system,
            response_model=response_model,
            temperature=temperature,
            session_id=session_id,
        )

    async def _call(
        self,
        model: str,
        prompt: str,
        system: str,
        response_model: Optional[Type[T]],
        temperature: float,
        session_id: str,
    ) -> str | T:
        """Internal call with retry, structured output, and telemetry."""
        start_time = time.monotonic()
        tokens_used = 0

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential(multiplier=1, min=1, max=8),
                retry=retry_if_exception_type(_RETRYABLE_OPENAI_ERRORS),
                reraise=True,
            ):
                with attempt:
                    messages = [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ]

                    kwargs: dict = {
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                    }

                    # Structured output mode
                    if response_model is not None:
                        kwargs["response_format"] = {"type": "json_object"}

                    response = await self._client.chat.completions.create(**kwargs)
                    tokens_used = response.usage.total_tokens if response.usage else 0
                    content = response.choices[0].message.content or ""

                    # Parse structured output
                    if response_model is not None:
                        import json
                        parsed = response_model.model_validate(json.loads(content))
                        return parsed

                    return content

        except (APIConnectionError, RateLimitError, TimeoutError) as exc:
            raise LLMUnavailableError(provider="openai") from exc
        except APIStatusError as exc:
            if exc.status_code in (500, 502, 503, 529):
                raise LLMUnavailableError(provider="openai", status_code=exc.status_code) from exc
            raise
        finally:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            logger.info(
                "llm_call",
                model=model,
                tokens=tokens_used,
                latency_ms=latency_ms,
                session_id=session_id,
                structured=response_model is not None,
            )

        raise LLMUnavailableError(provider="openai")  # Should never reach here

    async def stream_primary(
        self,
        prompt: str,
        system: str = "You are an expert banking relationship manager AI assistant.",
        session_id: str = "unknown",
    ):
        """
        Async generator streaming gpt-4o tokens for SSE endpoints.
        Yields (token_str, is_final) tuples.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        try:
            async with self._client.chat.completions.stream(
                model=self._primary_model,
                messages=messages,
                temperature=0.4,
            ) as stream:
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        yield delta, False
                yield "", True
        except Exception as exc:
            logger.error("llm_stream_error", error=str(exc), session_id=session_id)
            raise LLMUnavailableError(provider="openai") from exc


@lru_cache(maxsize=1)
def get_llm_router() -> LLMRouter:
    """Singleton LLM router — instantiated once per process."""
    return LLMRouter()
