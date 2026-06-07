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
        # Enter mock mode if: key is placeholder, key is missing
        use_mock = (
            not _s.OPENAI_API_KEY
            or _s.OPENAI_API_KEY.startswith("sk-...")
            or _s.OPENAI_API_KEY.startswith("sk-test-")
        )

        if not use_mock:
            # Try real OpenAI first; fall back to mock on any failure
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
            try:
                stream = await self._client.chat.completions.create(
                    model=self._primary_model,
                    messages=messages,
                    temperature=0.4,
                    stream=True,
                )
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content, False
                yield "", True
                return
            except Exception as exc:
                logger.error(
                    "llm_stream_openai_failed",
                    error=str(exc),
                    session_id=session_id,
                )
                yield f"⚠️ OpenAI API Error: {str(exc)}. If you recently updated the .env file, please restart your FastAPI backend server (poetry run uvicorn...) to load the new environment variables.", False
                yield "", True
                return

        # ── Mock / demo mode ─────────────────────────────────────────────────
        logger.info("llm_mock_stream", session_id=session_id)
        
        # Extract the user's actual question if possible to avoid matching other text inside the template
        question = prompt
        if "RM's Question:" in prompt:
            parts = prompt.split("RM's Question:")
            if len(parts) > 1:
                question = parts[1]
                if "Portfolio Summary:" in question:
                    question = question.split("Portfolio Summary:")[0]
                elif "Relevant Context" in question:
                    question = question.split("Relevant Context")[0]
        
        prompt_lower = question.lower()

        if "interest" in prompt_lower and ("too high" in prompt_lower or "high" in prompt_lower or "objection" in prompt_lower or "say" in prompt_lower or "handle" in prompt_lower):
            text = (
                "Here is how a veteran Senior Relationship Manager would handle this interest rate objection. "
                "Remember, when a client objects to the rate, they are usually expressing a lack of perceived value or comparing it to irrelevant benchmarks. "
                "Never defend the rate directly. Validate, pivot to value/convenience, and compare against higher-cost alternatives.\n\n"
                "### 💼 The Senior RM Playbook: Handling the 'Rate is Too High' Objection\n\n"
                "#### 1. Validate & Empathize (Drop the defense)\n"
                "Acknowledge their concern immediately. It disarms the customer and positions you on their side.\n"
                "* **Script:** \"I completely understand, Aarav. A loan is a serious financial commitment, and you absolutely should look for the best value. It's smart to review the numbers closely.\"\n\n"
                "#### 2. Anchor on Preferential Status & CIBIL\n"
                "Remind them that their rate isn't standard; it's an elite, pre-approved rate customized for their profile.\n"
                "* **Script:** \"I want to emphasize that because of your excellent credit score of **780** and your corporate profile, the bank has unlocked our absolute lowest tier rate for you. This is a pre-approved, fast-track offer—there's no lengthy documentation or salary verification needed. The money is ready to be disbursed to your account instantly.\"\n\n"
                "#### 3. Compare with High-Cost Alternatives (Credit Cards)\n"
                "Highlight the alternative cost of capital. Contrast the personal loan rate with card interest rates.\n"
                "* **Script:** \"If you were to fund this wedding/renovation using a premium credit card, you would be looking at an annual interest rate of **36% to 42%**. By comparison, this pre-approved personal loan saves you more than half that interest expense, with structured, predictable monthly EMIs.\"\n\n"
                "#### 4. The Opportunity Cost Pivot (Leave investments alone)\n"
                "Frame the loan as a capital preservation tool. Advise them to keep their investments compounding.\n"
                "* **Script:** \"Alternatively, you could liquidate some of your mutual funds or stock portfolio, but those investments are currently compounding at a strong rate. It makes much more financial sense to let your wealth grow undisturbed and use this lower-cost credit to bridge your short-term cash needs.\"\n\n"
                "#### 5. Soft Close & Actionable Next Step\n"
                "Offer to adjust the tenure to make the EMI more comfortable, rather than dropping the rate.\n"
                "* **Script:** \"Let's do this: I can run the numbers for a 48-month versus a 60-month tenure. Often, extending it slightly drops the EMI to a point where it doesn't affect your monthly cash flow at all. Would a quick 5-minute call today at 4 PM work to finalize the figures?\"\n\n"
                "**RM Pro-Tip:** Maintain absolute composure. Frame this not as a sales pitch, but as a strategic cash-flow optimization discussion. You are helping them protect their wealth while enabling their life goals."
            )
        elif "hni" in prompt_lower or "wealth" in prompt_lower:
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
        elif "home loan" in prompt_lower or "home" in prompt_lower:
            text = (
                "**Home Loan Eligibility Criteria:**\n"
                "1. **CIBIL Score:** Minimum 700.\n"
                "2. **Minimum Age:** 21 years (max 60 at loan maturity).\n"
                "3. **Employment:** Salaried ≥2 years; Self-employed ≥3 years ITR.\n"
                "4. **LTV Ratio:** Up to 80% of property value.\n"
                "5. **FOIR:** Less than 50%.\n\n"
                "Current preferential rates start at **8.5% p.a.** for CIBIL 750+. "
                "Would you like me to run an eligibility check for a specific customer?"
            )
        elif "churn" in prompt_lower or "risk" in prompt_lower or "alert" in prompt_lower:
            text = (
                "🚨 **Churn Risk Alerts — Top 3 Customers:**\n\n"
                "1. **Kavya Reddy** — Churn Score: 87% | Signals: No login 45 days, reduced SIP, 2 complaints.\n"
                "2. **Aryan Mehta** — Churn Score: 74% | Signals: Transferred ₹2L to competitor FD, dormant credit card.\n"
                "3. **Preethi Nair** — Churn Score: 68% | Signals: Low balance trend, 3 failed auto-debits.\n\n"
                "**Suggested Action:** Prioritize call to Kavya Reddy today with a retention offer — fixed deposit at 7.8% or premium credit card upgrade."
            )
        elif "wedding" in prompt_lower or "event" in prompt_lower or "detect" in prompt_lower:
            text = (
                "📅 **Overnight Life Event Detections:**\n\n"
                "1. **Ishaan Verma** — WEDDING detected (confidence 89%) | Signals: Tanishq ₹1.2L, Grand Palace Banquet ₹85,000 within 30 days.\n"
                "2. **Neha Gupta** — HOME_PURCHASE detected (confidence 76%) | Signals: Building society registration fee, interior design store visits.\n\n"
                "Both customers are shortlisted in your priority queue. "
                "Personal Loan pre-approval messages are ready to review in their opportunity cards."
            )
        elif "summarize" in prompt_lower or "portfolio" in prompt_lower or "overview" in prompt_lower:
            text = (
                "📊 **Your Portfolio Summary:**\n\n"
                "- **Total Customers:** 10 active\n"
                "- **High Risk Alerts:** 0\n"
                "- **Low Risk Portfolios:** 8\n"
                "- **Average CIBIL Score:** 765\n"
                "- **Open Opportunities:** 6 (3 Personal Loan, 2 Home Loan, 1 Credit Card)\n"
                "- **Overnight Events Detected:** 2 (1 Wedding, 1 Home Purchase)\n\n"
                "Top priority customer today: **Aarav Sharma** (CIBIL 780, Corporate Professional) — Pre-approved Personal Loan offer ready."
            )
        else:
            text = (
                "Hello! I'm your RM Copilot. I can search our product catalogues, policy playbooks, or summarize your customer portfolio. "
                "I've analyzed your database and found active opportunities for Personal Loans (e.g., Ishaan Verma, Neha Gupta) driven by recent wedding venue expenditures.\n\n"
                "Try asking me:\n"
                "- *'What is the Personal Loan eligibility criteria?'*\n"
                "- *'Show HNI customers with wealth migration signals'*\n"
                "- *'Summarize my portfolio'*\n"
                "- *'Which customers have churn risk?'*"
            )

        import asyncio
        words = text.split(" ")
        for i, word in enumerate(words):
            yield word + (" " if i < len(words) - 1 else ""), False
            await asyncio.sleep(0.02)
        yield "", True


@lru_cache(maxsize=1)
def get_llm_router() -> LLMRouter:
    """Singleton LLM router — instantiated once per process."""
    return LLMRouter()
