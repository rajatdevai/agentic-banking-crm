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
        _s = get_settings()
        if _s.OPENAI_API_KEY.startswith("sk-...") or not _s.OPENAI_API_KEY:
            logger.info("llm_mock_call", model=model, prompt_len=len(prompt))
            prompt_lower = prompt.lower()
            import json
            
            # Check for reranker prompt
            if "top_chunk_ids" in prompt_lower or "relevance ranking" in prompt_lower:
                import re
                ids = re.findall(r'ID:\s*\"([^\"]+)\"', prompt) or re.findall(r'ID:\s*\'([^\'\s]+)\'', prompt)
                data = {"top_chunk_ids": ids[:5]}
                return json.dumps(data)
                
            # Check for explainability prompt
            if "explain" in prompt_lower or "why_selected" in prompt_lower or "event_explanation" in prompt_lower:
                data = {
                    "why_selected": "Customer matches life event indicators for wedding venue expenditures and jewellery transactions.",
                    "event_explanation": "Detected large transactions at Grand Palace Banquet and Tanishq Jewellers.",
                    "product_rationale": "Personal Loan recommended for immediate wedding/celebration financing needs.",
                    "conversion_reasoning": "High predicted conversion probability (98%) based on transaction patterns in the demographic.",
                    "rm_action": "Outreach to customer via WhatsApp or call within 2 days with pre-approved interest rates."
                }
                return json.dumps(data)

            # Check for outreach generation
            if "whatsapp" in prompt_lower:
                return "Hi! We noticed you recently booking venues for your wedding. Congratulations! To support you during this special phase, bank is offering a pre-approved personal loan of up to ₹5 Lakhs with instant disbursal. Let me know if you would like to know more! - Priya Sharma, your RM"
            elif "email" in prompt_lower:
                return "Subject: Pre-approved Personal Loan Offer for your Upcoming Celebration\n\nDear Customer,\n\nI hope this email finds you well.\n\nWe noticed recent wedding planning transactions on your account. Congratulations on this major milestone! To support your requirements during this celebration, we are pleased to offer you a pre-approved Personal Loan of up to ₹5,000,000 with a competitive interest rate and flexible repayment options.\n\nKey highlights:\n- Instant disbursal to your account\n- Zero processing fees for this offer\n- Flexible tenure up to 60 months\n\nPlease let me know a convenient time to speak or call me directly to assist you.\n\nWarm regards,\nPriya Sharma\nRelationship Manager"
            elif "sms" in prompt_lower:
                return "Hi! Congratulate on your wedding planning! Get pre-approved personal loan up to ₹5 Lakhs instantly at special interest rates. Reply to check details."

            # Fallback
            if response_model is not None:
                return response_model.model_validate({})
            return "Mock response from local offline AI router."

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
        _s = get_settings()
        if _s.OPENAI_API_KEY.startswith("sk-...") or not _s.OPENAI_API_KEY:
            logger.info("llm_mock_stream", session_id=session_id)
            prompt_lower = prompt.lower()
            
            # Determine mock text based on prompt
            if "hni" in prompt_lower or "wealth" in prompt_lower:
                text = (
                    "Certainly! I have scanned your portfolio for HNI customers showing wealth migration signals. "
                    "The most prominent alert is for **Rajesh Kapoor** (Priya Sharma's portfolio). "
                    "He recently executed an outward international wire transfer of **₹1.5 Million** to an offshore account with the note 'Outward transfer to offshore capital group'.\n\n"
                    "**Recommendation:**\n"
                    "1. Contact Rajesh within 24 hours.\n"
                    "2. Position our Premium Wealth Management Suite and high-yield foreign currency deposit accounts to retain capital.\n"
                    "3. Offer a dedicated wealth advisory session."
                )
            elif "rahul" in prompt_lower:
                text = (
                    "Based on the outreach history, Rahul has not responded to the last two WhatsApp messages. "
                    "Since Rahul is a **Corporate Professional** with high investments, he may prefer a more structured email communication or a phone call during non-office hours.\n\n"
                    "**Suggested Action:**\n"
                    "- Change outreach channel to **Email**.\n"
                    "- Use a professional, benefit-driven tone highlighting the pre-approved personal loan rates (10.5%) and flexible EMI options.\n"
                    "- Send a calendar invite for a quick 5-minute call."
                )
            elif "eligibility" in prompt_lower or "criteria" in prompt_lower:
                text = (
                    "According to the Product Catalogue and Credit Policy guidelines:\n\n"
                    "**Personal Loan Eligibility Criteria:**\n"
                    "1. **CIBIL Score:** Minimum 720 (750+ preferred for best interest rates).\n"
                    "2. **Average Monthly Salary:** Minimum ₹50,000 (net credit).\n"
                    "3. **Relationship Tenure:** Minimum 6 months of active account status.\n"
                    "4. **KYC Status:** Must be 'COMPLETE' with no pending documentation.\n"
                    "5. **FOIR (Fixed Obligation to Income Ratio):** Less than 45%.\n\n"
                    "Would you like me to check the eligibility of any specific customer from your queue?"
                )
            else:
                text = (
                    "Hello! I'm your RM Copilot. I can search our product catalogues, policy playbooks, or summarize your customer portfolio. "
                    "I've analyzed your database and found active opportunities for Personal Loans (e.g., Ishaan Verma, Neha Gupta) driven by recent wedding venue expenditures. "
                    "Let me know how I can assist you with your customer outreach or query!"
                )
                
            # Stream the text
            import asyncio
            words = text.split(" ")
            for i, word in enumerate(words):
                yield word + (" " if i < len(words) - 1 else ""), False
                await asyncio.sleep(0.02)
            yield "", True
            return

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
