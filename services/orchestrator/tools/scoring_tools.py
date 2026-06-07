"""
Scoring tools — clean interface for ML model predictions.

Agents call these functions; they never import from services/ml/ directly.

Public API:
    get_conversion_probability(customer_id, event_type, db, redis) → float
    get_churn_probability(customer_id, db, redis) → float

Fallback heuristics (when model is not loaded):
    conversion_prob = 0.5 + (credit_score - 700) / 1000, capped at [0.05, 0.95]
    churn_prob = 0.1 if balance trending up else 0.4

ScoringModelNotLoadedError is raised only when the model file is completely
absent AND auto-training fails. In all other cases, the heuristic is applied.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class ScoringModelNotLoadedError(Exception):
    """Raised when an ML model cannot be loaded or trained."""


async def get_conversion_probability(
    customer_id: str,
    event_type: str,
    db=None,
    redis=None,
) -> float:
    """
    Predict conversion probability for a customer-event pair.

    Pipeline:
        1. get_or_compute features from feature store (Redis cache → DB)
        2. Call XGBoost model predict()
        3. On failure → deterministic heuristic fallback

    Args:
        customer_id: UUID string
        event_type: Event type string (e.g. "wedding", "home_purchase")
        db: AsyncSession for DB fallback if cache miss
        redis: Async Redis client for feature cache

    Returns:
        float in [0.0, 1.0] — conversion probability
    """
    features = await _get_features(customer_id, db, redis)
    if features is None:
        return _heuristic_conversion(None)

    try:
        from services.ml.models.conversion_score.model import predict
        prob = predict(features, event_type=event_type)
        logger.debug(
            "conversion_probability_computed",
            customer_id=customer_id,
            event_type=event_type,
            prob=round(prob, 3),
            source="xgboost",
        )
        return prob

    except Exception as exc:
        logger.warning(
            "conversion_model_failed_using_heuristic",
            customer_id=customer_id,
            error=str(exc),
        )
        return _heuristic_conversion(features)


async def get_churn_probability(
    customer_id: str,
    db=None,
    redis=None,
) -> float:
    """
    Predict churn probability for a customer.

    Pipeline:
        1. get_or_compute features from feature store
        2. Call LightGBM model predict()
        3. On failure → deterministic heuristic fallback

    Args:
        customer_id: UUID string
        db: AsyncSession
        redis: Async Redis client

    Returns:
        float in [0.0, 1.0] — churn probability
    """
    features = await _get_features(customer_id, db, redis)
    if features is None:
        return _heuristic_churn(None)

    try:
        from services.ml.models.churn_score.model import predict
        prob = predict(features)
        logger.debug(
            "churn_probability_computed",
            customer_id=customer_id,
            prob=round(prob, 3),
            source="lightgbm",
        )
        return prob

    except Exception as exc:
        logger.warning(
            "churn_model_failed_using_heuristic",
            customer_id=customer_id,
            error=str(exc),
        )
        return _heuristic_churn(features)


async def _get_features(
    customer_id: str,
    db,
    redis,
) -> Optional[np.ndarray]:
    """Retrieve or compute feature vector for a customer."""
    try:
        from services.ml.features.feature_store import get_or_compute_features
        return await get_or_compute_features(customer_id, redis=redis, db=db)
    except Exception as exc:
        logger.warning("feature_retrieval_failed", customer_id=customer_id, error=str(exc))
        return None


def _heuristic_conversion(features: Optional[np.ndarray]) -> float:
    """
    Documented fallback when model is unavailable.
    Formula: 0.5 + (credit_score - 700) / 1000, capped at [0.05, 0.95]

    Feature index 4 = credit_score
    """
    if features is None:
        return 0.35   # Conservative default

    credit_score = float(features[4]) if len(features) > 4 else 700.0
    prob = 0.5 + (credit_score - 700.0) / 1000.0
    return float(np.clip(prob, 0.05, 0.95))


def _heuristic_churn(features: Optional[np.ndarray]) -> float:
    """
    Documented fallback when churn model is unavailable.
    Formula: 0.1 if avg_balance trending up (salary_growth_rate > 0) else 0.4

    Feature index 10 = salary_growth_rate
    Feature index 12 = days_since_last_rm_interaction
    """
    if features is None:
        return 0.25   # Conservative default

    salary_growth = float(features[10]) if len(features) > 10 else 0.0
    days_since_rm = float(features[12]) if len(features) > 12 else 180.0

    if salary_growth > 0 and days_since_rm < 90:
        return 0.10   # Trending up + recent RM contact → low churn
    elif salary_growth < 0 or days_since_rm > 180:
        return 0.40   # Declining + no RM contact → elevated churn
    return 0.20       # Neutral
