"""
Scoring tools — ML model integration for conversion probability.

Provides get_conversion_probability() which calls the XGBoost model server.
Falls back with a clear ValueError if the model is not loaded.

Phase 5 will implement the full feature pipeline and model loading.
"""

from __future__ import annotations

from typing import Optional

import structlog

from shared.models.agent_state import CustomerProfile, DetectedEvent
from shared.constants.enums import ProductType

logger = structlog.get_logger(__name__)

# Module-level model cache — loaded on first call, None until Phase 5
_xgboost_model = None


async def get_conversion_probability(
    customer_profile: CustomerProfile,
    event: DetectedEvent,
    product: ProductType,
) -> float:
    """
    Call the XGBoost conversion model for a customer-event-product triple.

    Returns:
        float: conversion probability 0.0 to 1.0

    Raises:
        ValueError: if the model is not loaded (triggers heuristic fallback in caller)
    """
    if _xgboost_model is None:
        raise ValueError(
            "XGBoost conversion model not loaded. "
            "Run Phase 5 model training and call load_scoring_models() at startup."
        )

    # Phase 5 implementation: extract features and call model
    # features = await _build_feature_vector(customer_profile, event, product)
    # prob = float(_xgboost_model.predict_proba([features])[0][1])
    # return prob

    raise ValueError("Model not yet connected — Phase 5 required")


def load_scoring_models() -> None:
    """
    Load XGBoost model from disk at startup.
    Call this from the gateway lifespan handler after Phase 5.
    """
    global _xgboost_model
    try:
        import xgboost as xgb
        import os
        model_path = os.path.join(
            os.path.dirname(__file__),
            "../../ml/models/conversion_score/model.ubj"
        )
        if os.path.exists(model_path):
            _xgboost_model = xgb.Booster()
            _xgboost_model.load_model(model_path)
            logger.info("xgboost_model_loaded", path=model_path)
        else:
            logger.warning("xgboost_model_not_found", path=model_path)
    except Exception as exc:
        logger.error("xgboost_model_load_failed", error=str(exc))
