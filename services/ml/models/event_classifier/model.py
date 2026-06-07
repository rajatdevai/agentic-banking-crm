"""
Event Classifier — sklearn RandomForestClassifier for soft event signal detection.

Purpose:
    This classifier supplements the deterministic rule engine (EventDetectionAgent).
    It catches weak signals that the rule engine's threshold-based rules miss —
    e.g. a customer spending slightly below the jewellery threshold but also
    showing honeymoon travel spend and new household goods purchases.

Input: transaction feature aggregates for the last 90 days
Output: ranked list of (event_type, confidence_score) pairs

Feature vector for event classification (15 features):
    [0]  jewellery_mcc_spend       (MCC 5094, 5944)
    [1]  banquet_mcc_spend         (MCC 7922, 7999)
    [2]  travel_mcc_spend          (MCC 4511, 4722, 5962)
    [3]  furniture_mcc_spend       (MCC 5712, 5021)
    [4]  medical_mcc_spend         (MCC 8011, 8099, 5122)
    [5]  education_mcc_spend       (MCC 8220, 8299)
    [6]  gst_mcc_spend             (MCC 9311 — GST payments)
    [7]  salary_growth_rate
    [8]  avg_balance_trend         (balance vs 3-month average)
    [9]  txn_count_change          (count change MoM)
    [10] credit_score
    [11] relationship_tenure_months
    [12] debit_to_credit_ratio
    [13] salary_avg_3m
    [14] total_investments
"""

from __future__ import annotations

import os
import pickle
import threading
from pathlib import Path

import numpy as np
import structlog

from shared.constants.enums import EventType

logger = structlog.get_logger(__name__)

_model = None
_model_lock = threading.Lock()

MODEL_PATH = "services/ml/models/event_classifier/model.pkl"

EVENT_CLASSES = [
    EventType.WEDDING,
    EventType.HOME_PURCHASE,
    EventType.FOREIGN_EDUCATION,
    EventType.CHILD_EDUCATION,
    EventType.MEDICAL,
    EventType.BUSINESS_EXPANSION,
    EventType.PROMOTION,
    EventType.WEALTH_MIGRATION,
    EventType.RETIREMENT_PLANNING,
]

EVENT_FEATURE_DIM = 15


def predict_events(
    event_features: np.ndarray,
    top_k: int = 3,
    min_confidence: float = 0.15,
) -> list[tuple[EventType, float]]:
    """
    Predict likely life events from transaction feature aggregates.

    Args:
        event_features: np.ndarray of shape (15,) — event-specific features
        top_k: Maximum number of events to return
        min_confidence: Minimum confidence threshold to include an event

    Returns:
        List of (EventType, confidence_score) sorted by confidence descending.
        Confidence scores are calibrated probability estimates from the forest.
    """
    model = _get_or_train_model()

    try:
        from sklearn.ensemble import RandomForestClassifier

        proba = model.predict_proba(event_features.reshape(1, -1))[0]
        # proba is shape (n_classes,) — one prob per event class
        results = []
        for idx, event_type in enumerate(EVENT_CLASSES):
            if idx < len(proba) and proba[idx] >= min_confidence:
                results.append((event_type, float(proba[idx])))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    except Exception as exc:
        logger.error("event_classifier_predict_failed", error=str(exc))
        return []


def _get_or_train_model():
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model

        if os.path.exists(MODEL_PATH):
            _model = _load_model(MODEL_PATH)
        else:
            logger.info("event_classifier_not_found_training")
            _model = _train_and_save(MODEL_PATH)

    return _model


def _load_model(path: str):
    with open(path, "rb") as f:
        model = pickle.load(f)
    logger.info("event_classifier_loaded", path=path)
    return model


def _train_and_save(model_path: str):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.calibration import CalibratedClassifierCV

    logger.info("training_event_classifier", samples=5000)
    X, y = _generate_event_training_data(n_samples=5000)

    rf = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )

    # Calibrate to get proper probability estimates
    calibrated = CalibratedClassifierCV(rf, cv=3, method="isotonic")
    calibrated.fit(X, y)

    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    with open(model_path, "wb") as f:
        pickle.dump(calibrated, f)

    logger.info("event_classifier_trained_and_saved", path=model_path)
    return calibrated


def _generate_event_training_data(n_samples: int = 5000):
    """
    Synthetic event classification training data.
    y = most likely event type index (0 = wedding, 1 = home_purchase, etc.)
    """
    rng = np.random.default_rng(seed=77)
    n_classes = len(EVENT_CLASSES)
    X = np.zeros((n_samples, EVENT_FEATURE_DIM), dtype=np.float32)
    y = np.zeros(n_samples, dtype=np.int32)

    for i in range(n_samples):
        event_idx = rng.integers(0, n_classes)
        y[i] = event_idx

        # Base noise on all features
        row = rng.uniform(0, 5000, size=EVENT_FEATURE_DIM).astype(np.float32)

        # Event-specific signal patterns
        if event_idx == 0:  # WEDDING
            row[0] = rng.uniform(30_000, 150_000)  # jewellery
            row[1] = rng.uniform(20_000, 80_000)   # banquet
            row[2] = rng.uniform(10_000, 50_000)   # travel
        elif event_idx == 1:  # HOME_PURCHASE
            row[3] = rng.uniform(50_000, 200_000)  # furniture
            row[12] = rng.uniform(0.5, 1.2)        # DTI rising
        elif event_idx == 2:  # FOREIGN_EDUCATION
            row[5] = rng.uniform(50_000, 300_000)  # education spend
            row[2] = rng.uniform(30_000, 100_000)  # travel
        elif event_idx == 3:  # CHILD_EDUCATION
            row[5] = rng.uniform(20_000, 100_000)  # education spend
        elif event_idx == 4:  # MEDICAL
            row[4] = rng.uniform(20_000, 200_000)  # medical spend
        elif event_idx == 5:  # BUSINESS_EXPANSION
            row[6] = rng.uniform(50_000, 500_000)  # GST payments
            row[9] = rng.uniform(0.1, 0.5)         # txn count up
        elif event_idx == 6:  # PROMOTION
            row[7] = rng.uniform(0.1, 0.4)         # salary growth
            row[8] = rng.uniform(0.1, 0.5)         # balance trend up
        elif event_idx == 7:  # WEALTH_MIGRATION
            row[8] = rng.uniform(-0.4, -0.1)       # balance declining
            row[13] = rng.uniform(0, 30_000)        # low salary avg
        elif event_idx == 8:  # RETIREMENT_PLANNING
            row[10] = rng.uniform(650, 800)         # high credit score
            row[11] = rng.uniform(60, 120)          # long tenure
            row[14] = rng.uniform(500_000, 5_000_000)  # high investments

        X[i] = row

    logger.info("event_training_data_generated", samples=n_samples, classes=n_classes)
    return X, y
