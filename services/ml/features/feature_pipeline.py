"""
Feature Pipeline — assembles flat numerical feature vectors for ML models.

Queries the database and computes 21 features per customer:
    [0]  salary_avg_3m
    [1]  avg_balance_3m
    [2]  total_investments
    [3]  total_liabilities
    [4]  credit_score
    [5]  relationship_tenure_months
    [6]  event_count_last_30d
    [7]  product_holdings_count
    [8]  txn_count_last_90d
    [9]  avg_txn_amount_last_90d
    [10] salary_growth_rate           (MoM % change, last 3 months)
    [11] debit_to_credit_ratio        (last 90 days)
    [12] days_since_last_rm_interaction
    [13] previous_conversions_count
    [14] risk_tier_encoded            (low=0, medium=1, high=2)
    [15] persona_corporate_professional (one-hot)
    [16] persona_young_it_professional  (one-hot)
    [17] persona_startup_founder        (one-hot)
    [18] persona_doctor                 (one-hot)
    [19] persona_hni                    (one-hot)
    [20] persona_other                  (one-hot, catch-all)

All features are returned as a numpy float32 array of shape (21,).
Missing / NULL values are replaced with safe defaults (0 or median estimates).
"""

from __future__ import annotations

import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import func, select, and_, Float, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from shared.constants.enums import (
    OpportunityStatus,
    PersonaType,
    RiskTier,
    TransactionDirection,
)
from shared.db.models import (
    Customer,
    CustomerProfile,
    DetectedEvent,
    Opportunity,
    OutreachCampaign,
    Transaction,
)

logger = structlog.get_logger(__name__)

# Feature vector length — update FEATURE_DIM if adding features
FEATURE_DIM = 21

_RISK_TIER_MAP = {RiskTier.LOW: 0, RiskTier.MEDIUM: 1, RiskTier.HIGH: 2}

_PERSONA_ONE_HOT_ORDER = [
    PersonaType.CORPORATE_PROFESSIONAL,
    PersonaType.YOUNG_IT_PROFESSIONAL,
    PersonaType.STARTUP_FOUNDER,
    PersonaType.DOCTOR,
    PersonaType.HNI,
]


async def compute_features(
    customer_id: str,
    db: AsyncSession,
) -> np.ndarray:
    """
    Assemble the feature vector for a single customer.

    Args:
        customer_id: UUID string of the customer
        db: Async SQLAlchemy session

    Returns:
        np.ndarray of shape (FEATURE_DIM,) with dtype float32
        Safe defaults are used for missing / NULL values.
    """
    now = datetime.now(timezone.utc)
    cutoff_90d = now - timedelta(days=90)
    cutoff_30d = now - timedelta(days=30)
    cutoff_3m = now - timedelta(days=90)

    features = np.zeros(FEATURE_DIM, dtype=np.float32)

    try:
        # ------------------------------------------------------------------ #
        # Profile features
        # ------------------------------------------------------------------ #
        profile_result = await db.execute(
            select(CustomerProfile).where(CustomerProfile.customer_id == customer_id)
        )
        profile: Optional[CustomerProfile] = profile_result.scalar_one_or_none()

        customer_result = await db.execute(
            select(Customer).where(Customer.id == customer_id)
        )
        customer: Optional[Customer] = customer_result.scalar_one_or_none()

        if profile:
            features[0] = float(profile.salary_avg_3m or 0)
            features[1] = float(profile.avg_balance_3m or 0)
            features[2] = float(profile.total_investments or 0)
            features[3] = float(profile.total_liabilities or 0)
            features[4] = float(profile.credit_score or 650)
            features[7] = float(len(profile.product_holdings or {}))

        if customer:
            features[5] = float(customer.relationship_tenure_months or 0)
            features[14] = float(_RISK_TIER_MAP.get(customer.risk_tier, 0))

            # Persona one-hot encoding [15..20]
            persona = customer.persona_type
            for i, p in enumerate(_PERSONA_ONE_HOT_ORDER):
                features[15 + i] = 1.0 if persona == p else 0.0
            # features[20] = 1 if persona is not in the named list (catch-all)
            features[20] = 1.0 if persona not in _PERSONA_ONE_HOT_ORDER else 0.0

        # ------------------------------------------------------------------ #
        # Event count last 30 days
        # ------------------------------------------------------------------ #
        event_result = await db.execute(
            select(func.count(DetectedEvent.id)).where(
                and_(
                    DetectedEvent.customer_id == customer_id,
                    DetectedEvent.detected_at >= cutoff_30d,
                )
            )
        )
        features[6] = float(event_result.scalar() or 0)

        # ------------------------------------------------------------------ #
        # Transaction features (last 90 days)
        # ------------------------------------------------------------------ #
        txn_result = await db.execute(
            select(
                func.count(Transaction.id).label("count"),
                func.avg(Transaction.amount).label("avg_amount"),
                func.sum(
                    Transaction.amount.cast(Float) *
                    Transaction.direction.in_([TransactionDirection.DEBIT]).cast(Integer)
                ).label("total_debit"),
                func.sum(
                    Transaction.amount.cast(Float) *
                    Transaction.direction.in_([TransactionDirection.CREDIT]).cast(Integer)
                ).label("total_credit"),
            ).where(
                and_(
                    Transaction.customer_id == customer_id,
                    Transaction.txn_at >= cutoff_90d,
                )
            )
        )
        txn_row = txn_result.fetchone()

        if txn_row:
            features[8] = float(txn_row.count or 0)
            features[9] = float(txn_row.avg_amount or 0)

        # Debit/credit ratio — fallback to 1.0 (balanced) if no credit
        total_debit = float(txn_row.total_debit or 0) if txn_row else 0.0
        total_credit = float(txn_row.total_credit or 1.0) if txn_row else 1.0
        features[11] = total_debit / max(total_credit, 1.0)

        # ------------------------------------------------------------------ #
        # Salary growth rate — compare M1 credit vs M3 credit for salary MCCs
        # ------------------------------------------------------------------ #
        features[10] = await _compute_salary_growth(customer_id, now, db)

        # ------------------------------------------------------------------ #
        # Days since last RM interaction
        # ------------------------------------------------------------------ #
        last_outreach_result = await db.execute(
            select(func.max(OutreachCampaign.sent_at))
            .join(Opportunity, OutreachCampaign.opportunity_id == Opportunity.id)
            .where(Opportunity.customer_id == customer_id)
        )
        last_sent = last_outreach_result.scalar()
        if last_sent:
            features[12] = float((now - last_sent.replace(tzinfo=timezone.utc)).days)
        else:
            features[12] = 365.0  # No interaction on record → treat as 365 days

        # ------------------------------------------------------------------ #
        # Previous conversions count
        # ------------------------------------------------------------------ #
        conv_result = await db.execute(
            select(func.count(Opportunity.id)).where(
                and_(
                    Opportunity.customer_id == customer_id,
                    Opportunity.status == OpportunityStatus.CONVERTED,
                )
            )
        )
        features[13] = float(conv_result.scalar() or 0)

    except Exception as exc:
        logger.error("feature_pipeline_error", customer_id=str(customer_id), error=str(exc))
        # Return zero vector — caller should handle gracefully

    return features


async def _compute_salary_growth(
    customer_id: str,
    now: datetime,
    db: AsyncSession,
) -> float:
    """
    Compute month-over-month salary growth rate.
    Compares average credit amount in M1 (last 30 days) vs M3 (60-90 days ago).
    Returns percentage change, capped at ±2.0 (±200%).
    """
    # Salary MCC codes: 6022 (bank payroll), other salary MCCs
    SALARY_MCCS = ("6022", "6020", "6011")

    m1_start = now - timedelta(days=30)
    m3_start = now - timedelta(days=90)
    m3_end = now - timedelta(days=60)

    try:
        m1_result = await db.execute(
            select(func.avg(Transaction.amount)).where(
                and_(
                    Transaction.customer_id == customer_id,
                    Transaction.txn_at >= m1_start,
                    Transaction.direction == TransactionDirection.CREDIT,
                    Transaction.merchant_category.in_(SALARY_MCCS),
                )
            )
        )
        m1_avg = float(m1_result.scalar() or 0)

        m3_result = await db.execute(
            select(func.avg(Transaction.amount)).where(
                and_(
                    Transaction.customer_id == customer_id,
                    Transaction.txn_at >= m3_start,
                    Transaction.txn_at < m3_end,
                    Transaction.direction == TransactionDirection.CREDIT,
                    Transaction.merchant_category.in_(SALARY_MCCS),
                )
            )
        )
        m3_avg = float(m3_result.scalar() or 0)

        if m3_avg > 0:
            growth = (m1_avg - m3_avg) / m3_avg
            return float(np.clip(growth, -2.0, 2.0))
    except Exception:
        pass
    return 0.0


def feature_names() -> list[str]:
    """Return human-readable names for each feature index."""
    return [
        "salary_avg_3m",
        "avg_balance_3m",
        "total_investments",
        "total_liabilities",
        "credit_score",
        "relationship_tenure_months",
        "event_count_last_30d",
        "product_holdings_count",
        "txn_count_last_90d",
        "avg_txn_amount_last_90d",
        "salary_growth_rate",
        "debit_to_credit_ratio",
        "days_since_last_rm_interaction",
        "previous_conversions_count",
        "risk_tier_encoded",
        "persona_corporate_professional",
        "persona_young_it_professional",
        "persona_startup_founder",
        "persona_doctor",
        "persona_hni",
        "persona_other",
    ]
