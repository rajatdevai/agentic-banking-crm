"""
Feature Store — Redis cache for computed feature vectors.

Cache strategy:
    Key:   rag:features:{customer_id}
    Value: JSON-encoded float list (numpy array → list → JSON → bytes)
    TTL:   4 hours (refreshed by daily_scoring task before each run)

On cache miss → caller must call feature_pipeline.compute_features()
and then store the result via cache_features().

Interfaces:
    get_features(customer_id, redis)  → np.ndarray | None
    cache_features(customer_id, features, redis)
    invalidate_features(customer_id, redis)
    invalidate_all_features(redis)    → int (count of keys deleted)
"""

from __future__ import annotations

import json
from typing import Optional

import numpy as np
import structlog

from services.ml.features.feature_pipeline import FEATURE_DIM

logger = structlog.get_logger(__name__)

_CACHE_TTL_SECONDS = 4 * 3600  # 4 hours
_KEY_PREFIX = "features"


def _make_key(customer_id: str) -> str:
    return f"rag:{_KEY_PREFIX}:{customer_id}"


async def get_features(
    customer_id: str,
    redis,
) -> Optional[np.ndarray]:
    """
    Retrieve cached feature vector from Redis.

    Returns:
        np.ndarray of shape (FEATURE_DIM,) if cache hit, else None.
    """
    if redis is None:
        return None

    try:
        key = _make_key(customer_id)
        raw = await redis.get(key)
        if raw is None:
            return None

        data = json.loads(raw)
        arr = np.array(data, dtype=np.float32)

        if arr.shape != (FEATURE_DIM,):
            logger.warning(
                "feature_cache_shape_mismatch",
                customer_id=customer_id,
                expected=FEATURE_DIM,
                got=arr.shape,
            )
            return None

        return arr

    except Exception as exc:
        logger.warning("feature_cache_get_failed", customer_id=customer_id, error=str(exc))
        return None


async def cache_features(
    customer_id: str,
    features: np.ndarray,
    redis,
) -> bool:
    """
    Store feature vector in Redis with 4-hour TTL.

    Args:
        customer_id: Customer UUID string
        features: np.ndarray of shape (FEATURE_DIM,)
        redis: Async Redis client

    Returns:
        True if stored successfully, False if Redis is unavailable.
    """
    if redis is None:
        return False

    try:
        key = _make_key(customer_id)
        data = json.dumps(features.tolist())
        await redis.setex(key, _CACHE_TTL_SECONDS, data)
        return True
    except Exception as exc:
        logger.warning("feature_cache_set_failed", customer_id=customer_id, error=str(exc))
        return False


async def invalidate_features(customer_id: str, redis) -> bool:
    """
    Delete the cached feature vector for a single customer.
    Called by daily_scoring before recomputing features.

    Returns:
        True if key was deleted (existed), False otherwise.
    """
    if redis is None:
        return False

    try:
        key = _make_key(customer_id)
        result = await redis.delete(key)
        return bool(result)
    except Exception as exc:
        logger.warning("feature_invalidate_failed", customer_id=customer_id, error=str(exc))
        return False


async def invalidate_all_features(redis) -> int:
    """
    Delete ALL feature cache entries (rag:features:*).
    Called by daily_scoring at the start of each nightly run.

    Returns:
        Number of keys deleted.
    """
    if redis is None:
        return 0

    try:
        pattern = f"rag:{_KEY_PREFIX}:*"
        keys = await redis.keys(pattern)
        if not keys:
            return 0
        count = await redis.delete(*keys)
        logger.info("feature_cache_cleared", count=count)
        return count
    except Exception as exc:
        logger.error("feature_cache_clear_failed", error=str(exc))
        return 0


async def get_or_compute_features(
    customer_id: str,
    redis,
    db,
) -> np.ndarray:
    """
    Convenience function: return cached features or compute and cache them.

    Args:
        customer_id: Customer UUID string
        redis: Async Redis client (can be None)
        db: AsyncSession (required for DB queries)

    Returns:
        np.ndarray of shape (FEATURE_DIM,)
    """
    from services.ml.features.feature_pipeline import compute_features

    # Try cache first
    cached = await get_features(customer_id, redis)
    if cached is not None:
        return cached

    # Compute from DB
    features = await compute_features(customer_id, db)

    # Store in cache (fire-and-forget — don't block on failure)
    await cache_features(customer_id, features, redis)

    return features
