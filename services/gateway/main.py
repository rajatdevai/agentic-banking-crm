"""
RM Copilot Gateway — FastAPI application entry point.

Startup sequence:
    1. Load and validate settings (fails fast if secrets missing)
    2. Connect to Redis (required for rate limiter, PII vault, cache)
    3. Register middleware in correct order:
         RateLimiterMiddleware → AuditMiddleware → PIIMaskMiddleware
    4. Mount all routers
    5. Health endpoint checks DB and Redis connectivity

Middleware execution order (outermost → innermost):
    Request:  RateLimiter → Audit → PIIMask → Router
    Response: Router → PIIMask → Audit (latency measured here) → RateLimiter

Structured logging:
    Every log line contains: timestamp, trace_id, rm_id, level, message.
    Uses structlog with JSON renderer in production, pretty console in development.

Run locally:
    uvicorn services.gateway.main:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from services.gateway.middleware.audit import AuditMiddleware
from services.gateway.middleware.pii_mask import PIIMaskMiddleware
from services.gateway.middleware.rate_limiter import RateLimiterMiddleware
from services.gateway.routers import auth, chat, customers, outreach
from shared.config.settings import get_settings
from shared.db.session import engine



# ---------------------------------------------------------------------------
# Structured logging setup
# ---------------------------------------------------------------------------
def _configure_logging() -> None:
    """
    Configure structlog for structured JSON logging in production and
    human-friendly console output in development.
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
    ]

    try:
        _s = get_settings()
        app_env = _s.APP_ENV
        log_level = _s.LOG_LEVEL
    except Exception:
        # Fallback defaults when settings are not yet fully configured (e.g. tests)
        app_env = "development"
        log_level = "INFO"

    renderer = (
        structlog.processors.JSONRenderer()
        if app_env == "production"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


_configure_logging()
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Application lifespan — startup and shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager — manages resource lifecycle.
    Resources created here are available on app.state throughout the process.
    """
    _s = get_settings()
    logger.info("gateway_starting", env=_s.APP_ENV)

    # Connect to Redis
    redis_client = aioredis.from_url(
        _s.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        await redis_client.ping()
        logger.info("redis_connected", url=_s.REDIS_URL)
    except Exception as exc:
        logger.error("redis_connection_failed", error=str(exc))
        # Don't fail startup — Redis unavailability degrades gracefully
        redis_client = None

    app.state.redis = redis_client

    logger.info("gateway_ready", host="0.0.0.0", port=8000)

    yield  # Application runs here

    # Shutdown
    logger.info("gateway_shutting_down")
    if redis_client:
        await redis_client.aclose()
    await engine.dispose()
    logger.info("gateway_stopped")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="RM Copilot — Banking Intelligence Platform",
    description=(
        "AI-powered Relationship Manager assistant. "
        "Identifies high-value customer opportunities, detects life events, "
        "scores conversion probability, and generates personalized outreach. "
        "\n\n**All endpoints require Bearer token authentication.**"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — development origins only (restrict to actual frontend URLs in prod)
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Trace-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)

# ---------------------------------------------------------------------------
# Middleware stack (registered last = runs outermost = runs first on request)
# Registration order matters: rate limiter must be outermost
# ---------------------------------------------------------------------------
app.add_middleware(AuditMiddleware)
app.add_middleware(RateLimiterMiddleware)

# PII masking middleware (selective routes only — see PIIMaskMiddleware.MASK_ROUTES)
@app.middleware("http")
async def pii_mask_middleware(request: Request, call_next):
    """
    Thin inline wrapper for PIIMaskMiddleware that accesses redis from app.state.
    PIIMaskMiddleware requires a redis_client at construction time, so we
    instantiate it per-request using app.state.redis.
    """
    redis = request.app.state.redis
    if redis and request.url.path in PIIMaskMiddleware.MASK_ROUTES:
        masker_middleware = PIIMaskMiddleware(app=app, redis_client=redis)
        return await masker_middleware.dispatch(request, call_next)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(outreach.router)
app.include_router(chat.router)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------
@app.get(
    "/health",
    tags=["System"],
    summary="Health check — DB and Redis connectivity",
    description="Pinged by load balancers and uptime monitors. Returns 200 if healthy.",
)
async def health_check(request: Request):
    """
    Checks:
    - Database: runs SELECT 1 against the cloud Postgres
    - Redis: runs PING
    Returns 200 if both are healthy, 503 if either is degraded.
    """
    db_status = "unknown"
    redis_status = "unknown"
    overall_healthy = True

    # Check database
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as db_exc:
        db_status = f"error: {str(db_exc)[:100]}"
        overall_healthy = False
        logger.error("health_check_db_failed", error=str(db_exc))

    # Check Redis
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        redis_status = "not_configured"
        # Redis is non-critical for basic operation — don't mark unhealthy
    else:
        try:
            await redis.ping()
            redis_status = "connected"
        except Exception as redis_exc:
            redis_status = f"error: {str(redis_exc)[:100]}"
            overall_healthy = False
            logger.error("health_check_redis_failed", error=str(redis_exc))

    response_body = {
        "status": "healthy" if overall_healthy else "degraded",
        "version": "1.0.0",
        "environment": get_settings().APP_ENV,
        "dependencies": {
            "database": db_status,
            "redis": redis_status,
        },
    }

    return JSONResponse(
        content=response_body,
        status_code=200 if overall_healthy else 503,
    )


# ---------------------------------------------------------------------------
# Global exception handler — structured error responses
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error(
        "unhandled_exception",
        trace_id=trace_id,
        path=request.url.path,
        error=str(exc),
        error_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An unexpected error occurred. Please try again or contact support.",
            "trace_id": trace_id,
        },
    )
