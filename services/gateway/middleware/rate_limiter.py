"""
Redis sliding-window rate limiter middleware.

Rate limits are per-RM per route-category per minute bucket.
Two tiers of limits:

    STANDARD routes (all endpoints): 100 requests/minute
    LLM_HEAVY routes (copilot, outreach generate): 10 requests/minute

LLM-heavy routes are throttled more aggressively because they:
    1. Make OpenAI API calls (real cost per call)
    2. Have higher latency (2-5s each)
    3. Can exhaust token budgets if hammered accidentally

Counter key format:
    ratelimit:{rm_id}:{route_category}:{minute_bucket}
    minute_bucket = int(unix_timestamp / 60)

    Example: ratelimit:uuid-abc:llm_heavy:28234780

Sliding window implementation:
    We use Redis INCR + EXPIRE for approximate sliding window counters.
    True sliding window (using ZADD/ZRANGEBYSCORE) would be more accurate
    but ~10x more Redis operations per request. For rate limiting, approximate
    is sufficient and the simpler implementation is more reliable.

Response when limit exceeded:
    HTTP 429 Too Many Requests
    Retry-After header: seconds until the current minute bucket resets
"""

import time
import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Route category classification
# ---------------------------------------------------------------------------
_LLM_HEAVY_PREFIXES: tuple[str, ...] = (
    "/copilot/chat",
    "/outreach/generate",
)

_EXCLUDED_FROM_RATELIMIT: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/auth/login",   # Login must not be rate-limited by this middleware
                     # (it has its own brute-force protection)
})

# Limits: (requests_per_minute)
_STANDARD_LIMIT = 100
_LLM_HEAVY_LIMIT = 10


def _get_route_category(path: str) -> str:
    """Classify a route path into a rate limit category."""
    for prefix in _LLM_HEAVY_PREFIXES:
        if path.startswith(prefix):
            return "llm_heavy"
    return "standard"


def _current_minute_bucket() -> int:
    """Returns the current minute bucket as an integer (floor of unix_ts / 60)."""
    return int(time.time() / 60)


def _seconds_until_next_bucket() -> int:
    """Seconds remaining until the next minute bucket starts."""
    current_second_in_minute = int(time.time()) % 60
    return 60 - current_second_in_minute


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """
    Per-RM sliding-window rate limiter backed by Redis.

    Requires a Redis client to be available on app.state.redis.
    This is initialised in the gateway main.py lifespan handler.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip excluded routes
        if request.url.path in _EXCLUDED_FROM_RATELIMIT:
            return await call_next(request)

        # Extract RM identity — rate limit by RM, not by IP
        rm_id = getattr(request.state, "rm_id", None)
        if rm_id is None:
            # Unauthenticated request — let auth middleware reject it
            return await call_next(request)

        redis = getattr(request.app.state, "redis", None)
        if redis is None:
            # Redis unavailable — fail open (don't block RM work)
            logger.warning("rate_limiter_redis_unavailable", rm_id=rm_id)
            return await call_next(request)

        category = _get_route_category(request.url.path)
        limit = _LLM_HEAVY_LIMIT if category == "llm_heavy" else _STANDARD_LIMIT
        bucket = _current_minute_bucket()
        key = f"ratelimit:{rm_id}:{category}:{bucket}"

        try:
            current_count = await redis.incr(key)
            if current_count == 1:
                # First request in this bucket — set TTL (bucket expires in 2 mins max)
                await redis.expire(key, 120)
        except Exception as redis_exc:
            # Redis error — fail open with log
            logger.error("rate_limiter_redis_error", error=str(redis_exc), rm_id=rm_id)
            return await call_next(request)

        if current_count > limit:
            retry_after = _seconds_until_next_bucket()
            logger.warning(
                "rate_limit_exceeded",
                rm_id=rm_id,
                category=category,
                count=current_count,
                limit=limit,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded for {category} requests. "
                        f"Limit: {limit} per minute. "
                        f"Try again in {retry_after} seconds."
                    ),
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Category": category,
                },
            )

        # Attach rate limit headers to normal responses too
        response = await call_next(request)
        remaining = max(0, limit - current_count)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Category"] = category
        return response
