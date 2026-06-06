# Synthetic customer data generator for tests.
# Generates realistic but entirely FICTIONAL customer profiles and transaction histories.
# No real PII ever. All names, phone numbers, and account numbers are synthesised.
# Used in: unit tests, integration tests, local seed_db.py, and demo scenarios.

import uuid
import random
from datetime import datetime, timedelta
from shared.constants.enums import PersonaType, RiskTier, KYCStatus, EventType
from shared.models.customer import CustomerProfile


def make_customer(
    persona_type: PersonaType = PersonaType.YOUNG_PROFESSIONAL,
    risk_tier: RiskTier = RiskTier.LOW,
    credit_score: int = 750,
    relationship_tenure_months: int = 24,
) -> CustomerProfile:
    """Generate a single synthetic CustomerProfile for testing."""
    return CustomerProfile(
        customer_id=str(uuid.uuid4()),
        rm_id=str(uuid.uuid4()),
        persona_type=persona_type,
        risk_tier=risk_tier,
        kyc_status=KYCStatus.COMPLETE,
        relationship_tenure_months=relationship_tenure_months,
        salary_avg_3m=random.uniform(50_000, 200_000),
        avg_balance_3m=random.uniform(20_000, 500_000),
        total_investments=random.uniform(0, 2_000_000),
        total_liabilities=random.uniform(0, 1_000_000),
        credit_score=credit_score,
        product_holdings={"savings_account": True, "credit_card": "Signature"},
        behavioral_tags=["investor", "travel_heavy"] if persona_type == PersonaType.HNI else ["salary_earner"],
        last_refreshed_at=datetime.utcnow(),
    )


def make_wedding_customer() -> CustomerProfile:
    """Pre-configured synthetic customer showing wedding signals — for demo use case 1."""
    return make_customer(
        persona_type=PersonaType.YOUNG_PROFESSIONAL,
        risk_tier=RiskTier.LOW,
        credit_score=762,
        relationship_tenure_months=18,
    )


def make_hni_customer() -> CustomerProfile:
    """Pre-configured HNI customer for wealth advisory demo — for demo use case 2."""
    return make_customer(
        persona_type=PersonaType.HNI,
        risk_tier=RiskTier.LOW,
        credit_score=810,
        relationship_tenure_months=60,
    )


def make_startup_founder() -> CustomerProfile:
    """Pre-configured startup founder for business expansion demo — for demo use case 3."""
    return make_customer(
        persona_type=PersonaType.STARTUP_FOUNDER,
        risk_tier=RiskTier.MEDIUM,
        credit_score=720,
        relationship_tenure_months=12,
    )


# Ready-to-use fixtures for the three primary demo scenarios
DEMO_CUSTOMERS = [
    make_wedding_customer(),
    make_hni_customer(),
    make_startup_founder(),
]
