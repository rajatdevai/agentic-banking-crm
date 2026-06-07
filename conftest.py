"""
Pytest configuration and shared fixtures for the RM Copilot test suite.

Sets required environment variables before any imports happen, so
pydantic-settings validators don't fail with missing secrets during testing.
"""

import os

# Set test environment variables before any app module is imported.
# These must be set before pydantic-settings reads them.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test_rmcopilot")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy-key-for-unit-tests")
os.environ.setdefault("SECRET_KEY", "test-secret-key-minimum-16-chars-abc")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("APP_ENV", "test")

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# SQLAlchemy custom compilers for SQLite compatibility in tests
# ---------------------------------------------------------------------------
import json
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, ARRAY, UUID

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(element, compiler, **kw):
    return "TEXT"

@compiles(ARRAY, "sqlite")
def compile_array_sqlite(element, compiler, **kw):
    return "TEXT"

@compiles(UUID, "sqlite")
def compile_uuid_sqlite(element, compiler, **kw):
    return "CHAR(36)"

# Bind/result processors for SQLite JSONB
original_jsonb_bind = JSONB.bind_processor
def new_jsonb_bind(self, dialect):
    if dialect.name == "sqlite":
        return lambda value: json.dumps(value) if value is not None else None
    return original_jsonb_bind(self, dialect)
JSONB.bind_processor = new_jsonb_bind

original_jsonb_result = JSONB.result_processor
def new_jsonb_result(self, dialect, coltype):
    if dialect.name == "sqlite":
        return lambda value: json.loads(value) if value is not None else None
    return original_jsonb_result(self, dialect, coltype)
JSONB.result_processor = new_jsonb_result

# Bind/result processors for SQLite ARRAY
original_array_bind = ARRAY.bind_processor
def new_array_bind(self, dialect):
    if dialect.name == "sqlite":
        return lambda value: json.dumps(value) if value is not None else None
    return original_array_bind(self, dialect)
ARRAY.bind_processor = new_array_bind

original_array_result = ARRAY.result_processor
def new_array_result(self, dialect, coltype):
    if dialect.name == "sqlite":
        return lambda value: json.loads(value) if value is not None else None
    return original_array_result(self, dialect, coltype)
ARRAY.result_processor = new_array_result


# Bind/result processors for SQLite UUID
import uuid as python_uuid
original_uuid_bind = UUID.bind_processor
def new_uuid_bind(self, dialect):
    if dialect.name == "sqlite":
        return lambda value: str(value) if value is not None else None
    return original_uuid_bind(self, dialect)
UUID.bind_processor = new_uuid_bind

original_uuid_result = UUID.result_processor
def new_uuid_result(self, dialect, coltype):
    if dialect.name == "sqlite":
        return lambda value: python_uuid.UUID(value) if value is not None else None
    return original_uuid_result(self, dialect, coltype)
UUID.result_processor = new_uuid_result



