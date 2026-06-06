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
