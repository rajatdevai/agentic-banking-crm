"""
Conversion Score Model — XGBoost binary classifier.

Predicts P(customer converts | features + event_type).

Training data (5,000 synthetic samples):
    Positive signals (high conversion prob):
        - High salary (> 80,000)
        - Low debt-to-income (< 40%)
        - CIBIL score > 720
        - Wedding or home_purchase event detected
        - No existing personal / home loan in product_holdings
        - Young IT professional or doctor persona

    Negative signals (low conversion prob):
        - High risk tier (risk_tier = 2)
        - High DTI (> 60%)
        - Low credit score (< 680)
        - WEDDING event but customer already has personal loan

Model is saved to settings.CONVERSION_MODEL_PATH on first train.
Lazy loading: model is loaded from disk on first predict() call.
Auto-training: if model file not found, trains from synthetic data.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# Thread-safe lazy load
_model = None
_model_lock = threading.Lock()

# Event type → integer encoding for the feature vector
EVENT_TYPE_ENCODING = {
    "wedding": 0,
    "home_purchase": 1,
    "foreign_education": 2,
    "child_education": 3,
    "medical": 4,
    "business_expansion": 5,
    "promotion": 6,
    "wealth_migration": 7,
    "retirement_planning": 8,
    "new_born": 9,
    "unknown": 10,
}


def predict(features: np.ndarray, event_type: str = "unknown") -> float:
    """
    Predict conversion probability for a customer.

    Args:
        features: Feature vector from feature_pipeline.compute_features()
        event_type: String event type (e.g. "wedding", "home_purchase")

    Returns:
        float: Conversion probability in [0.0, 1.0]
    """
    model = _get_or_train_model()

    # Append event type encoding to feature vector
    event_code = EVENT_TYPE_ENCODING.get(event_type.lower(), 10)
    full_features = np.append(features, float(event_code)).reshape(1, -1)

    try:
        import xgboost as xgb
        dmatrix = xgb.DMatrix(full_features)
        prob = float(model.predict(dmatrix)[0])
        return float(np.clip(prob, 0.0, 1.0))
    except Exception as exc:
        logger.error("conversion_model_predict_failed", error=str(exc))
        raise


def _get_or_train_model():
    """Thread-safe lazy model loader with auto-training fallback."""
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model

        model_path = _get_model_path()
        if os.path.exists(model_path):
            _model = _load_model(model_path)
        else:
            logger.info("conversion_model_not_found_training_from_synthetic_data",
                        path=model_path)
            _model = _train_and_save(model_path)

    return _model


def _get_model_path() -> str:
    try:
        from shared.config.settings import get_settings
        return get_settings().CONVERSION_MODEL_PATH
    except Exception:
        return "services/ml/models/conversion_score/model.ubj"


def _load_model(path: str):
    import xgboost as xgb
    model = xgb.Booster()
    model.load_model(path)
    logger.info("conversion_model_loaded", path=path)
    return model


def _train_and_save(model_path: str):
    """
    Generate 5000 synthetic samples and train the XGBoost conversion model.
    Feature vector has FEATURE_DIM + 1 (event_type_code) features.
    """
    import xgboost as xgb
    from services.ml.features.feature_pipeline import FEATURE_DIM

    logger.info("generating_synthetic_training_data", samples=5000)
    X, y = _generate_synthetic_data(n_samples=5000)

    dtrain = xgb.DMatrix(X, label=y)

    params = {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "max_depth": 6,
        "learning_rate": 0.1,
        "n_estimators": 200,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 5,
        "scale_pos_weight": 3,   # Class imbalance: ~25% positive
        "seed": 42,
    }

    model = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=200,
        verbose_eval=False,
    )

    # Save model
    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    model.save_model(model_path)
    logger.info("conversion_model_trained_and_saved", path=model_path, samples=5000)
    return model


def _generate_synthetic_data(n_samples: int = 5000):
    """
    Generate synthetic training data with realistic feature distributions.

    Features (22 dims = 21 from pipeline + event_type_code):
        Positive class conditions (conversion = 1):
            - CIBIL >= 720 AND salary >= 60k AND event is wedding/home → high prob
            - Promotion + no existing loan → medium-high prob
            - Medical emergency → medium prob

        Negative class conditions (conversion = 0):
            - risk_tier == HIGH → very low
            - DTI > 0.6 → low
            - credit_score < 680 → low
    """
    rng = np.random.default_rng(seed=42)
    n_features = 22  # 21 from pipeline + 1 event code

    X = np.zeros((n_samples, n_features), dtype=np.float32)
    y = np.zeros(n_samples, dtype=np.float32)

    for i in range(n_samples):
        # Base features
        salary = rng.uniform(25_000, 2_00_000)
        balance = salary * rng.uniform(0.5, 8.0)
        investments = salary * rng.uniform(0, 20)
        liabilities = salary * rng.uniform(0, 5)
        credit_score = rng.integers(580, 820)
        tenure = rng.integers(0, 120)
        event_count = rng.integers(0, 4)
        products = rng.integers(0, 6)
        txn_count = rng.integers(5, 60)
        avg_txn = salary * rng.uniform(0.01, 0.5)
        salary_growth = rng.uniform(-0.1, 0.2)
        dti = liabilities / max(salary * 12, 1)
        days_since_rm = rng.integers(0, 365)
        conversions = rng.integers(0, 5)
        risk_tier = rng.choice([0, 1, 2], p=[0.6, 0.3, 0.1])
        # Persona one-hot (5 named + 1 other)
        persona_idx = rng.integers(0, 6)
        persona_oh = np.zeros(6, dtype=np.float32)
        persona_oh[persona_idx] = 1.0
        # Event type
        event_type = rng.integers(0, 11)

        X[i, :15] = [salary, balance, investments, liabilities, credit_score,
                     tenure, event_count, products, txn_count, avg_txn,
                     salary_growth, dti, days_since_rm, conversions, risk_tier]
        X[i, 15:21] = persona_oh
        X[i, 21] = float(event_type)

        # --- Label computation ---
        prob = 0.15  # base conversion rate

        # Positive signals
        if credit_score >= 720:
            prob += 0.25
        if credit_score >= 750:
            prob += 0.10
        if salary >= 80_000:
            prob += 0.15
        if event_type in (0, 1):  # wedding, home_purchase
            prob += 0.20
        if event_type == 6:  # promotion
            prob += 0.15
        if event_count > 0:
            prob += 0.10
        if products < 2:  # few products → more headroom
            prob += 0.10
        if persona_idx == 1:  # young IT professional
            prob += 0.08
        if persona_idx == 3:  # doctor
            prob += 0.08
        if conversions > 0:  # previous conversion → higher trust
            prob += 0.10

        # Negative signals
        if risk_tier == 2:  # HIGH risk
            prob -= 0.30
        if dti > 0.6:
            prob -= 0.20
        if credit_score < 680:
            prob -= 0.25
        if days_since_rm > 180:
            prob -= 0.10

        # Add noise and threshold
        prob = float(np.clip(prob + rng.normal(0, 0.05), 0.0, 1.0))
        y[i] = 1.0 if rng.random() < prob else 0.0

    positive_rate = y.mean()
    logger.info("synthetic_data_generated",
                samples=n_samples, positive_rate=round(float(positive_rate), 3))
    return X, y
