"""
Async SQLAlchemy engine and session factory for the RM Copilot platform.

Design decisions:
- asyncpg driver: fastest PostgreSQL async driver for Python
- NullPool for serverless/cloud connections (Neon/Supabase close idle connections)
  Switch to AsyncConnectionPool if running on a persistent compute instance
- get_db(): async generator for FastAPI Depends() injection
- All sessions are scoped to a single request — never share sessions across requests
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Database configuration pulled from environment / .env file."""

    DATABASE_URL: str
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 40

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


_settings = DatabaseSettings()

# ---------------------------------------------------------------------------
# Engine
# NullPool is used because cloud-hosted Postgres (Neon, Supabase) aggressively
# closes idle connections. NullPool creates a fresh connection per request and
# releases it immediately — avoids "connection closed unexpectedly" errors.
# For a persistent server with stable DB connections, swap to:
#   engine = create_async_engine(url, pool_size=..., max_overflow=...)
# ---------------------------------------------------------------------------
engine = create_async_engine(
    _settings.DATABASE_URL,
    poolclass=NullPool,
    echo=False,          # Set to True for SQL query logging during development
    future=True,
)

# ---------------------------------------------------------------------------
# Session factory
# expire_on_commit=False: prevents SQLAlchemy from expiring loaded attributes
# after commit, which would cause lazy-load errors in async context.
# ---------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency — yields an async database session scoped to the request.

    Usage:
        @router.get("/customers")
        async def list_customers(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
