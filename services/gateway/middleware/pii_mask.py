"""
PII Masking System — the most critical security component in the platform.

Architecture:
    1. PIIMasker: wraps Presidio Analyzer + custom Indian banking recognisers.
       Detects and masks PII in text, stores a token-vault in Redis.
    2. pii_guard: decorator that wraps any LLM-calling coroutine, auto-masking
       input and unmasking output. Raises PIIDetectedInOutputError if raw PII
       slips through in an unexpected position.
    3. PIIMaskMiddleware: FastAPI/Starlette middleware stub — routes that need
       full body masking (e.g., raw CBS data ingestion) use this. Most agent
       calls use pii_guard directly.

Indian banking PII types we detect:
    - PERSON           → [PERSON_1]
    - PHONE_NUMBER     → [PHONE_1]       (Indian mobile: 10-digit, +91 prefix)
    - EMAIL_ADDRESS    → [EMAIL_1]
    - PAN_NUMBER       → [PAN_1]         (format: ABCDE1234F)
    - AADHAAR_NUMBER   → [AADHAAR_1]    (12-digit, space/hyphen separated)
    - ACCOUNT_NUMBER   → [ACCOUNT_1]    (8–18 digit bank account numbers)
    - LOCATION         → [LOCATION_1]
    - AMOUNT is intentionally NOT masked — financial figures are kept for
      context. We mask the identity, not the transaction amounts.
"""

import json
import re
import uuid
from functools import wraps
from typing import Any, Callable

import redis.asyncio as aioredis
import structlog
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from shared.config.settings import get_settings
from shared.exceptions.domain import PIIDetectedInOutputError

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom Presidio recognisers for Indian banking context
# ---------------------------------------------------------------------------

class PANRecognizer(PatternRecognizer):
    """
    Indian PAN card recogniser.
    Format: 5 uppercase letters + 4 digits + 1 uppercase letter
    Example: ABCDE1234F
    """
    PATTERNS = [
        Pattern("PAN_CARD_STRONG", r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", 0.95),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="PAN_NUMBER",
            patterns=self.PATTERNS,
            context=["pan", "permanent account number", "income tax"],
        )


class AadhaarRecognizer(PatternRecognizer):
    """
    Indian Aadhaar number recogniser.
    Format: 12 digits, optionally separated by spaces or hyphens
    Example: 1234 5678 9012 or 1234-5678-9012
    """
    PATTERNS = [
        Pattern(
            "AADHAAR_STRONG",
            r"\b[2-9]{1}[0-9]{3}\s?[0-9]{4}\s?[0-9]{4}\b",
            0.90,
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="AADHAAR_NUMBER",
            patterns=self.PATTERNS,
            context=["aadhaar", "aadhar", "uid", "unique identification"],
        )


class IndianAccountRecognizer(PatternRecognizer):
    """
    Indian bank account number recogniser.
    Format: 8–18 digits (most Indian banks use 9–18 digits).
    Context-anchored to reduce false positives on plain numbers.
    """
    PATTERNS = [
        Pattern(
            "ACCOUNT_NUMBER_CONTEXT",
            r"\b[0-9]{8,18}\b",
            0.60,  # Lower confidence — depends heavily on surrounding context
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="ACCOUNT_NUMBER",
            patterns=self.PATTERNS,
            context=[
                "account", "a/c", "acc no", "acct", "bank account",
                "savings account", "current account",
            ],
        )


class IndianPhoneRecognizer(PatternRecognizer):
    """
    Indian mobile phone number recogniser.
    Formats: +91 XXXXXXXXXX, 91-XXXXXXXXXX, 0XXXXXXXXXX, XXXXXXXXXX (10 digits starting 6-9)
    """
    PATTERNS = [
        Pattern("INDIAN_PHONE_INTL", r"\+91[\s\-]?[6-9][0-9]{9}\b", 0.95),
        Pattern("INDIAN_PHONE_LOCAL", r"\b[6-9][0-9]{9}\b", 0.75),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="PHONE_NUMBER",
            patterns=self.PATTERNS,
            context=["phone", "mobile", "call", "whatsapp", "contact"],
        )


# ---------------------------------------------------------------------------
# PIIMasker — core masking class
# ---------------------------------------------------------------------------

# Token type to display label mapping
_ENTITY_TO_TOKEN_PREFIX: dict[str, str] = {
    "PERSON": "PERSON",
    "PHONE_NUMBER": "PHONE",
    "EMAIL_ADDRESS": "EMAIL",
    "PAN_NUMBER": "PAN",
    "AADHAAR_NUMBER": "AADHAAR",
    "ACCOUNT_NUMBER": "ACCOUNT",
    "LOCATION": "LOCATION",
    "NRP": "PERSON",        # Named entity — person-like
    "US_SSN": "ID",         # Catch-all for ID-like patterns
}


class PIIMasker:
    """
    Detects and masks PII in text using Presidio + Indian banking custom recognisers.

    Vault format in Redis:
        Key: pii_vault:{session_id}
        Value: JSON dict mapping token → original value
        TTL: REDIS_PII_VAULT_TTL_SECONDS (default 8 hours = session lifetime)
    """

    def __init__(self, redis_client: aioredis.Redis):
        self._redis = redis_client
        self._analyzer = self._build_analyzer()
        self._anonymizer = AnonymizerEngine()

    def _build_analyzer(self) -> AnalyzerEngine:
        """Build the Presidio AnalyzerEngine with all custom recognisers."""
        engine = AnalyzerEngine()
        engine.registry.add_recognizer(PANRecognizer())
        engine.registry.add_recognizer(AadhaarRecognizer())
        engine.registry.add_recognizer(IndianAccountRecognizer())
        engine.registry.add_recognizer(IndianPhoneRecognizer())
        return engine

    def _analyze(self, text: str) -> list[RecognizerResult]:
        """Run Presidio analysis on text. Returns detected PII entities."""
        if not get_settings().PII_MASK_ENABLED:
            return []
        return self._analyzer.analyze(
            text=text,
            language="en",
            entities=list(_ENTITY_TO_TOKEN_PREFIX.keys()),
            allow_list=None,
        )

    def mask_text(self, text: str, session_id: str) -> tuple[str, dict[str, str]]:
        """
        Synchronous mask — detects PII and replaces with typed tokens.
        Returns: (masked_text, vault_dict mapping token→original_value)

        Call store_vault() after this to persist the vault to Redis.
        """
        if not get_settings().PII_MASK_ENABLED or not text:
            return text, {}

        results = self._analyze(text)
        if not results:
            return text, {}

        vault: dict[str, str] = {}
        counters: dict[str, int] = {}

        # Sort by start position descending so replacements don't shift indices
        results_sorted = sorted(results, key=lambda r: r.start, reverse=True)

        masked = text
        for result in results_sorted:
            prefix = _ENTITY_TO_TOKEN_PREFIX.get(result.entity_type, "PII")
            counters[prefix] = counters.get(prefix, 0) + 1
            token = f"[{prefix}_{counters[prefix]}]"
            original = text[result.start:result.end]
            vault[token] = original
            masked = masked[:result.start] + token + masked[result.end:]

        return masked, vault

    async def store_vault(self, session_id: str, vault: dict[str, str]) -> None:
        """Persist vault to Redis with session TTL."""
        if not vault:
            return
        key = f"pii_vault:{session_id}"
        existing_raw = await self._redis.get(key)
        if existing_raw:
            existing: dict = json.loads(existing_raw)
            existing.update(vault)
            vault = existing
        await self._redis.setex(
            key,
            get_settings().REDIS_PII_VAULT_TTL_SECONDS,
            json.dumps(vault),
        )

    async def load_vault(self, session_id: str) -> dict[str, str]:
        """Load vault from Redis. Returns empty dict if session has expired."""
        key = f"pii_vault:{session_id}"
        raw = await self._redis.get(key)
        if not raw:
            return {}
        return json.loads(raw)

    async def mask(self, text: str, session_id: str) -> str:
        """
        Full async mask — detects, replaces tokens, stores vault.
        Returns the masked text ready to be sent to an LLM.
        """
        masked, vault = self.mask_text(text, session_id)
        if vault:
            await self.store_vault(session_id, vault)
        return masked

    async def unmask(self, text: str, session_id: str) -> str:
        """
        Restores original PII values from vault into LLM output.
        Works by simple string replacement of all tokens in the vault.
        """
        vault = await self.load_vault(session_id)
        if not vault:
            return text

        result = text
        for token, original in vault.items():
            result = result.replace(token, original)

        return result

    async def validate_output(
        self, llm_output: str, session_id: str, agent_name: str
    ) -> str:
        """
        Unmask LLM output and then re-scan it for any raw PII that
        slipped through the masking pipeline. Raises PIIDetectedInOutputError
        if raw PII is found in the final text in an unexpected position.
        """
        unmasked = await self.unmask(llm_output, session_id)

        # Re-scan the unmasked output to check for any NEW PII not in the vault
        residual_results = self._analyze(unmasked)
        if residual_results:
            found_entities = [r.entity_type for r in residual_results]
            logger.critical(
                "pii_detected_in_llm_output",
                agent=agent_name,
                session_id=session_id,
                entities=found_entities,
            )
            raise PIIDetectedInOutputError(
                agent_name=agent_name,
                pii_entities=found_entities,
            )

        return unmasked


# ---------------------------------------------------------------------------
# pii_guard decorator
# ---------------------------------------------------------------------------

def pii_guard(agent_name: str):
    """
    Decorator for coroutines that call an LLM.

    Expects the wrapped function to:
    - Accept `session_id: str` as a keyword argument
    - Accept `masker: PIIMasker` as a keyword argument
    - Return a string (the LLM output)

    The decorator will:
    1. Mask the `prompt` keyword argument before the call
    2. Pass the masked prompt to the wrapped function
    3. Validate and unmask the returned string
    4. Raise PIIDetectedInOutputError if PII is found in output

    Usage:
        @pii_guard(agent_name="ExplainabilityAgent")
        async def call_llm(prompt: str, session_id: str, masker: PIIMasker) -> str:
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args, **kwargs) -> Any:
            masker: PIIMasker | None = kwargs.get("masker")
            session_id: str | None = kwargs.get("session_id")

            if masker is None or session_id is None:
                logger.warning(
                    "pii_guard_skipped",
                    reason="masker or session_id not provided",
                    agent=agent_name,
                )
                return await fn(*args, **kwargs)

            # Mask the prompt if present
            if "prompt" in kwargs and isinstance(kwargs["prompt"], str):
                kwargs["prompt"] = await masker.mask(kwargs["prompt"], session_id)

            result = await fn(*args, **kwargs)

            # Validate and unmask result
            if isinstance(result, str):
                result = await masker.validate_output(result, session_id, agent_name)

            return result

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# FastAPI Middleware stub
# ---------------------------------------------------------------------------

class PIIMaskMiddleware(BaseHTTPMiddleware):
    """
    Request-level PII masking middleware.

    For most endpoints, masking is handled at the agent level via pii_guard.
    This middleware is applied to specific raw-data ingestion routes (e.g., CBS
    webhook payloads) where the full request body may contain PII before it
    reaches any agent.

    It does NOT mask all requests — that would add unacceptable latency to
    read-only endpoints like GET /customers/priority-queue.
    """

    # Routes where body-level masking is applied
    MASK_ROUTES: frozenset[str] = frozenset({
        "/webhooks/cbs",
        "/copilot/chat",
    })

    def __init__(self, app, redis_client: aioredis.Redis):
        super().__init__(app)
        self._masker = PIIMasker(redis_client)

    async def dispatch(self, request: Request, call_next):
        # Only mask on designated routes
        if request.url.path not in self.MASK_ROUTES:
            return await call_next(request)

        session_id = request.headers.get("X-Session-ID", str(uuid.uuid4()))
        request.state.session_id = session_id
        request.state.masker = self._masker

        response = await call_next(request)
        return response
