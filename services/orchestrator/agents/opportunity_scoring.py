"""
OpportunityScoringAgent — composite scoring with XGBoost + heuristic fallback.

Reads: customer_profile, detected_events, risk_assessment from state
Writes: opportunities (list[Opportunity], sorted descending by priority_score)

Priority score formula:
    (customer_value_score × 0.3) + (conversion_probability × 0.4)
    + (revenue_potential_score × 0.2) + (retention_importance × 0.1)

ML model: calls XGBoost conversion model via scoring_tools.
Fallback: heuristic scoring when model is unavailable — clearly documented.
"""

from __future__ import annotations

from typing import Optional

import structlog

from services.orchestrator.agents.base_agent import BaseAgent
from services.orchestrator.graph.state import AgentState
from shared.constants.enums import EventType, PersonaType, ProductType, RiskTier
from shared.models.agent_state import (
    CustomerProfile,
    DetectedEvent,
    Opportunity,
    RiskAssessment,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Event-to-product mapping: which products fit which life events
# ---------------------------------------------------------------------------
_EVENT_PRODUCT_MAP: dict[EventType, list[ProductType]] = {
    EventType.WEDDING:            [ProductType.PERSONAL_LOAN, ProductType.GOLD_LOAN],
    EventType.HOME_PURCHASE:      [ProductType.HOME_LOAN, ProductType.PERSONAL_LOAN],
    EventType.FOREIGN_EDUCATION:  [ProductType.EDUCATION_LOAN, ProductType.FOREX_CARD],
    EventType.PROMOTION:          [ProductType.WEALTH_ADVISORY, ProductType.MUTUAL_FUND, ProductType.PERSONAL_LOAN],
    EventType.BUSINESS_EXPANSION: [ProductType.WORKING_CAPITAL_LOAN, ProductType.CURRENT_ACCOUNT],
    EventType.MEDICAL:            [ProductType.PERSONAL_LOAN, ProductType.HEALTH_INSURANCE],
    EventType.RETIREMENT_PLANNING:[ProductType.MUTUAL_FUND, ProductType.WEALTH_ADVISORY, ProductType.FIXED_DEPOSIT],
    EventType.NEW_BORN:           [ProductType.CHILD_EDUCATION_PLAN, ProductType.HEALTH_INSURANCE],
    EventType.WEALTH_MIGRATION:   [ProductType.WEALTH_ADVISORY, ProductType.MUTUAL_FUND],
}

# Revenue potential by product (estimated annual margin in ₹)
_REVENUE_POTENTIAL: dict[ProductType, float] = {
    ProductType.HOME_LOAN:           150_000,
    ProductType.PERSONAL_LOAN:        25_000,
    ProductType.EDUCATION_LOAN:        30_000,
    ProductType.GOLD_LOAN:             10_000,
    ProductType.WEALTH_ADVISORY:      200_000,
    ProductType.MUTUAL_FUND:           50_000,
    ProductType.FIXED_DEPOSIT:         15_000,
    ProductType.WORKING_CAPITAL_LOAN: 100_000,
    ProductType.CURRENT_ACCOUNT:       20_000,
    ProductType.HEALTH_INSURANCE:      12_000,
    ProductType.FOREX_CARD:             5_000,
    ProductType.CHILD_EDUCATION_PLAN:  15_000,
}

# Maximum possible revenue for normalisation
_MAX_REVENUE = max(_REVENUE_POTENTIAL.values())


def _customer_value_score(cp: CustomerProfile) -> float:
    """Normalised 0-1 score based on salary band and investment holdings."""
    salary_score = 0.0
    if cp.salary_avg_3m:
        salary_score = min(1.0, cp.salary_avg_3m / 500_000)

    investment_score = 0.0
    if cp.total_investments:
        investment_score = min(1.0, cp.total_investments / 10_000_000)

    return (salary_score * 0.6) + (investment_score * 0.4)


def _heuristic_conversion_probability(
    cp: CustomerProfile,
    event: DetectedEvent,
    product: ProductType,
    risk: Optional[RiskAssessment],
) -> float:
    """
    Heuristic fallback for conversion probability.
    Used when XGBoost model is unavailable.

    This function is clearly marked as fallback in the returned Opportunity.
    Basis:
        - High confidence event → higher conversion
        - Good credit score → higher conversion for loan products
        - Existing relationship tenure → higher conversion
        - High risk tier → lower conversion
    """
    base = event.confidence_score * 0.5  # Event confidence as base

    # Tenure bonus (up to +0.2)
    tenure_bonus = min(0.2, cp.relationship_tenure_months / 120)
    base += tenure_bonus

    # Credit quality for loan products
    loan_products = {ProductType.HOME_LOAN, ProductType.PERSONAL_LOAN,
                     ProductType.EDUCATION_LOAN, ProductType.WORKING_CAPITAL_LOAN}
    if product in loan_products and risk:
        if risk.credit_score and risk.credit_score >= 750:
            base += 0.15
        elif risk.credit_score and risk.credit_score < 650:
            base -= 0.20

    # Risk tier penalty
    if risk and risk.risk_tier == RiskTier.HIGH:
        base *= 0.5

    return max(0.05, min(0.95, round(base, 3)))


def _retention_importance(cp: CustomerProfile) -> float:
    """
    Higher value = more important to retain this customer.
    Based on tenure + investment holdings (wealth migration risk).
    """
    tenure_score = min(1.0, cp.relationship_tenure_months / 60)
    wealth_score = 0.0
    if cp.total_investments and cp.total_investments > 500_000:
        wealth_score = min(1.0, cp.total_investments / 5_000_000)
    return (tenure_score * 0.5) + (wealth_score * 0.5)


class OpportunityScoringAgent(BaseAgent):
    agent_name = "OpportunityScoringAgent"
    timeout_seconds = 20.0

    async def execute(self, state: AgentState) -> dict:
        cp: Optional[CustomerProfile] = state.get("customer_profile")
        events: list[DetectedEvent] = state.get("detected_events") or []
        risk: Optional[RiskAssessment] = state.get("risk_assessment")

        if not cp or not events:
            logger.info("opportunity_scoring_skipped", reason="no customer profile or events")
            return {"opportunities": []}

        scored: list[Opportunity] = []

        for event in events:
            products = _EVENT_PRODUCT_MAP.get(event.event_type, [])

            for product in products:
                # Skip products customer already holds
                if cp.holds_product(product):
                    logger.debug("skipping_held_product", product=product.value)
                    continue

                # Eligibility gate for unsecured loans
                if risk and not risk.is_eligible_for_unsecured_loan():
                    loan_types = {ProductType.PERSONAL_LOAN, ProductType.EDUCATION_LOAN}
                    if product in loan_types:
                        logger.info(
                            "opportunity_filtered_risk",
                            product=product.value,
                            risk_tier=risk.risk_tier.value,
                        )
                        continue

                # Try ML model first, fall back to heuristic
                scoring_method = "heuristic_fallback"
                try:
                    from services.orchestrator.tools.scoring_tools import get_conversion_probability
                    conversion_prob = await get_conversion_probability(
                        customer_id=cp.customer_id,
                        event_type=event.event_type.value,
                        db=self._db,
                        redis=self._redis,
                    )
                    scoring_method = "xgboost"
                except Exception as model_exc:
                    logger.warning(
                        "xgboost_model_unavailable",
                        error=str(model_exc),
                        fallback="heuristic",
                    )
                    conversion_prob = _heuristic_conversion_probability(cp, event, product, risk)

                # Compute composite priority score
                cv_score = _customer_value_score(cp)
                revenue = _REVENUE_POTENTIAL.get(product, 10_000)
                rev_score = revenue / _MAX_REVENUE
                retention = _retention_importance(cp)

                priority_score = (
                    cv_score       * 0.3
                    + conversion_prob * 0.4
                    + rev_score      * 0.2
                    + retention      * 0.1
                )

                opportunity = Opportunity(
                    customer_id=cp.customer_id,
                    event_type=event.event_type,
                    product_recommended=product,
                    priority_score=round(priority_score, 4),
                    conversion_probability=conversion_prob,
                    revenue_potential=revenue,
                    risk_flags=risk.risk_flags if risk else {},
                    scoring_method=scoring_method,
                )
                scored.append(opportunity)

        # Sort descending by priority_score
        scored.sort(key=lambda o: o.priority_score, reverse=True)

        logger.info(
            "opportunity_scoring_complete",
            customer_id=cp.customer_id,
            opportunities_count=len(scored),
        )

        return {"opportunities": scored}
