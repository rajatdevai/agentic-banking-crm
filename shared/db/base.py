"""
SQLAlchemy 2.0 declarative base and reusable mixins.

All ORM models across the platform inherit from Base.
TimestampMixin adds created_at, updated_at, and soft-delete deleted_at
to every table that inherits it — never hard-delete records in this system.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base imported by all ORM models and Alembic env.py."""
    pass


class TimestampMixin:
    """
    Mixin providing created_at, updated_at, and soft-delete deleted_at columns.

    - created_at: set by the database server on INSERT, never changed
    - updated_at: set on INSERT and refreshed by the DB on every UPDATE
    - deleted_at: NULL means active; a timestamp means soft-deleted
      (never physically remove rows — required for audit trail integrity)
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
