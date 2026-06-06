"""
LangGraph checkpointer configuration.

Uses Redis for state persistence so:
    - RM can resume a conversation after a page refresh
    - Failed graph runs can be resumed from the last successful checkpoint
    - Graph state is auditable across sessions

Phase 4 uses the SqliteSaver as a local fallback when Redis is unavailable,
and RedisSaver when Redis is configured.
"""

from __future__ import annotations

import structlog
from langgraph.checkpoint.memory import MemorySaver

logger = structlog.get_logger(__name__)


def get_checkpointer(redis_client=None):
    """
    Return a LangGraph checkpointer appropriate for the environment.

    Priority order:
        1. Redis-backed checkpointer (production)
        2. In-memory checkpointer (development / when Redis unavailable)

    Args:
        redis_client: Optional async Redis client from app.state.redis

    Returns:
        A LangGraph-compatible checkpointer instance
    """
    if redis_client is not None:
        try:
            # langgraph-checkpoint-redis is the optional extra
            from langgraph.checkpoint.redis import AsyncRedisSaver
            checkpointer = AsyncRedisSaver(redis_client)
            logger.info("checkpointer_redis_enabled")
            return checkpointer
        except ImportError:
            logger.warning(
                "langgraph_redis_checkpointer_not_installed",
                hint="pip install langgraph-checkpoint-redis",
            )
        except Exception as exc:
            logger.warning("redis_checkpointer_init_failed", error=str(exc))

    # Fallback: in-memory (state lost on restart — acceptable for development)
    logger.info("checkpointer_memory_fallback")
    return MemorySaver()
