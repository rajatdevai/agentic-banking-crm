"""
Alembic migration environment for RM Copilot.

Configured for:
- Async SQLAlchemy engine (asyncpg driver)
- DATABASE_URL injected from environment / .env — never hardcoded here
- Autogenerate reads all models from shared/db/models.py via Base.metadata
- run_migrations_online() uses run_sync() to bridge async engine with sync Alembic API
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

# Load .env so DATABASE_URL is available when running alembic from the CLI
load_dotenv()

# ---------------------------------------------------------------------------
# Import Base and all models so Alembic autogenerate can detect them.
# If a model is not imported here (directly or transitively), autogenerate
# will not see its table and will generate DROP TABLE in the migration.
# ---------------------------------------------------------------------------
from shared.db.base import Base  # noqa: F401
import shared.db.models  # noqa: F401 — registers all ORM models against Base.metadata

# ---------------------------------------------------------------------------
# Alembic Config object — gives access to alembic.ini values
# ---------------------------------------------------------------------------
config = context.config

# Set up Python logging from alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Point autogenerate at our declarative Base
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# DATABASE_URL — pulled from environment, never from alembic.ini
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Copy .env.example to .env and fill in your cloud Postgres URL."
    )


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — generates SQL without a live DB connection.
    Useful for reviewing migration SQL before applying or for CI pipelines.
    """
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Inner sync function called inside the async engine's run_sync()."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # Render all enums as CREATE TYPE — required for PostgreSQL native enums
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations against the live database using the async engine.
    NullPool is used here for the same reason as in session.py — cloud Postgres
    closes idle connections, so we don't pool migration connections.
    """
    db_url = DATABASE_URL
    connect_args = {}
    if "sslmode=" in db_url or "neon.tech" in db_url or "supabase" in db_url:
        connect_args["ssl"] = True
        if "?" in db_url:
            db_url = db_url.split("?", 1)[0]

    connectable = create_async_engine(db_url, poolclass=pool.NullPool, connect_args=connect_args)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migration — bridges async engine with sync Alembic API."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
