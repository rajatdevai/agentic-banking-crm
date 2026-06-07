"""
Churn Score Model — LightGBM binary classifier.

Predicts P(customer churns in next 90 days).

Churn signals in synthetic training data:
    Positive class (churn = 1):
        - Declining balance trajectory (negative salary_growth_rate)
        - Low days_since_last_rm_interaction mapped inversely (>180 days → no RM contact)
        - Wealth migration event detected (event_type 7)
        - Debit-to-credit ratio > 1.3 (spending more than earning)
        - Declining txn_count or avg_txn_amount
        - High liabilities relative to investments
        - HNI with no recent contact (high-value at risk)

    Negative class (churn = 0):
        - Recent RM interaction (< 30 days)
        - Growing balance trajectory
        - Multiple conversions in history (loyal customer)
        - Long tenure (> 48 months)
        - Active SIP (captured in product_holdings_count)

LightGBM is faster than XGBoost for tabular data at this scale and handles
missing values natively (no imputation needed in production).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

_model = None
_model_lock = threading.Lock()


def predict(features: np.ndarray) -> float:
    """
    Predict churn probability for a customer.

    Args:
        features: Feature vector from feature_pipeline.compute_features()

    Returns:
        float: Churn probability in [0.0, 1.0]
    """
    model = _get_or_train_model()

    try:
        import lightgbm as lgb
        dataset = lgb.Dataset(features.reshape(1, -1), free_raw_data=False)
        prob = float(model.predict(features.reshape(1, -1))[0])
        return float(np.clip(prob, 0.0, 1.0))
    except Exception as exc:
        logger.error("churn_model_predict_failed", error=str(exc))
        raise


def _get_or_train_model():
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
            logger.info("churn_model_not_found_training_from_synthetic_data",
                        path=model_path)
            _model = _train_and_save(model_path)

    return _model


def _get_model_path() -> str:
    try:
        from shared.config.settings import get_settings
        return get_settings().CHURN_MODEL_PATH
    except Exception:
        return "services/ml/models/churn_score/model.ubj"


def _load_model(path: str):
    import lightgbm as lgb
    model = lgb.Booster(model_file=path)
    logger.info("churn_model_loaded", path=path)
    return model


def _train_and_save(model_path: str):
    import lightgbm as lgb

    logger.info("generating_synthetic_churn_training_data", samples=5000)
    X, y = _generate_churn_synthetic_data(n_samples=5000)

    train_data = lgb.Dataset(X, label=y)

    params = {
        "objective": "binary",
        "metric": "auc",
        "num_leaves": 63,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "min_child_samples": 20,
        "scale_pos_weight": 4,   # Churn is ~20% of customers
        "seed": 42,
        "verbose": -1,
    }

    model = lgb.train(
        params=params,
        train_set=train_data,
        num_boost_round=200,
    )

    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    model.save_model(model_path)
    logger.info("churn_model_trained_and_saved", path=model_path)
    return model


def _generate_churn_synthetic_data(n_samples: int = 5000):
    """
    Synthetic churn data: 21 features from feature pipeline.
    ~20% positive (churn) rate to match realistic banking churn.
    """
    rng = np.random.default_rng(seed=123)

    X = np.zeros((n_samples, 21), dtype=np.float32)
    y = np.zeros(n_samples, dtype=np.float32)

    for i in range(n_samples):
        salary = rng.uniform(25_000, 2_00_000)
        balance = salary * rng.uniform(0.2, 8.0)
        investments = salary * rng.uniform(0, 15)
        liabilities = salary * rng.uniform(0, 6)
        credit_score = rng.integers(580, 820)
        tenure = rng.integers(0, 120)
        event_count = rng.integers(0, 4)
        products = rng.integers(0, 6)
        txn_count = rng.integers(2, 60)
        avg_txn = salary * rng.uniform(0.005, 0.5)
        salary_growth = rng.uniform(-0.3, 0.3)
        dti = liabilities / max(salary * 12, 1)
        days_since_rm = rng.integers(0, 400)
        conversions = rng.integers(0, 5)
        risk_tier = rng.choice([0, 1, 2], p=[0.55, 0.30, 0.15])
        persona_idx = rng.integers(0, 6)
        persona_oh = np.zeros(6, dtype=np.float32)
        persona_oh[persona_idx] = 1.0

        X[i, :15] = [salary, balance, investments, liabilities, credit_score,
                     tenure, event_count, products, txn_count, avg_txn,
                     salary_growth, dti, days_since_rm, conversions, risk_tier]
        X[i, 15:] = persona_oh

        # --- Churn probability computation ---
        churn_prob = 0.10  # base churn rate

        # Churn signals
        if salary_growth < -0.05:   # Declining salary/income
            churn_prob += 0.15
        if days_since_rm > 180:     # No RM contact in 6 months
            churn_prob += 0.20
        if days_since_rm > 270:     # No contact in 9 months
            churn_prob += 0.10
        if dti > 1.3:               # Heavily over-leveraged
            churn_prob += 0.20
        if balance < salary * 0.5:  # Low balance relative to salary
            churn_prob += 0.10
        if products == 0:           # No products → no stickiness
            churn_prob += 0.15
        if persona_idx == 4:        # HNI with no RM contact → wealth migration risk
            if days_since_rm > 60:
                churn_prob += 0.20

        # Retention signals
        if tenure > 48:
            churn_prob -= 0.10
        if conversions > 1:
            churn_prob -= 0.15
        if days_since_rm < 30:
            churn_prob -= 0.15
        if salary_growth > 0.1:
            churn_prob -= 0.10
        if products >= 3:
            churn_prob -= 0.10

        # Noise + threshold
        churn_prob = float(np.clip(churn_prob + rng.normal(0, 0.04), 0.0, 1.0))
        y[i] = 1.0 if rng.random() < churn_prob else 0.0

    logger.info("churn_synthetic_data_generated",
                samples=n_samples, churn_rate=round(float(y.mean()), 3))
    return X, y
