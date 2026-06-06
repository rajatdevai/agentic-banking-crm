"""
Synthetic customer fixtures for integration and unit tests.

These are in-memory objects only — no DB required unless the test explicitly
calls seed_to_db(). Designed to cover specific test scenarios cleanly.

Usage:
    from tests.fixtures.synthetic_customers import WEDDING_CUSTOMER, seed_to_db
    
    async def test_something(db):
        customer = await seed_to_db(db, WEDDING_CUSTOMER)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class SyntheticTransaction:
    merchant_name: str
    merchant_category: str  # MCC code as string
    amount: float
    direction: str  # "debit" | "credit"
    days_ago: int
    notes: str = ""


@dataclass
class SyntheticCustomer:
    customer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    rm_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    persona_type: str = "young_it_professional"
    risk_tier: str = "low"
    kyc_status: str = "full"
    relationship_tenure_months: int = 24

    # Profile data
    salary_avg_3m: float = 80_000.0
    avg_balance_3m: float = 150_000.0
    total_investments: float = 200_000.0
    total_liabilities: float = 100_000.0
    credit_score: int = 740
    product_holdings: dict = field(default_factory=dict)
    behavioral_tags: list[str] = field(default_factory=list)

    # Transactions that will be seeded
    transactions: list[SyntheticTransaction] = field(default_factory=list)


# ---------------------------------------------------------------------------
# WEDDING_CUSTOMER — has jewellery + banquet signals
# ---------------------------------------------------------------------------
WEDDING_CUSTOMER = SyntheticCustomer(
    persona_type="young_it_professional",
    risk_tier="low",
    salary_avg_3m=90_000.0,
    avg_balance_3m=180_000.0,
    total_investments=250_000.0,
    credit_score=755,
    transactions=[
        # Jewellery spend — above wedding threshold
        SyntheticTransaction(
            merchant_name="Tanishq Jewellers",
            merchant_category="5094",   # Jewellery MCC
            amount=85_000.0,
            direction="debit",
            days_ago=45,
            notes="Wedding jewellery purchase",
        ),
        SyntheticTransaction(
            merchant_name="Senco Gold",
            merchant_category="5944",
            amount=40_000.0,
            direction="debit",
            days_ago=30,
            notes="Additional jewellery",
        ),
        # Banquet spend
        SyntheticTransaction(
            merchant_name="Grand Banquet Hall",
            merchant_category="7922",
            amount=65_000.0,
            direction="debit",
            days_ago=20,
            notes="Wedding venue booking advance",
        ),
        SyntheticTransaction(
            merchant_name="Royal Caterers",
            merchant_category="7999",
            amount=35_000.0,
            direction="debit",
            days_ago=15,
            notes="Catering advance",
        ),
        # Salary credits
        SyntheticTransaction(
            merchant_name="Employer Salary",
            merchant_category="6022",
            amount=90_000.0,
            direction="credit",
            days_ago=60,
        ),
        SyntheticTransaction(
            merchant_name="Employer Salary",
            merchant_category="6022",
            amount=90_000.0,
            direction="credit",
            days_ago=30,
        ),
        SyntheticTransaction(
            merchant_name="Employer Salary",
            merchant_category="6022",
            amount=90_000.0,
            direction="credit",
            days_ago=1,
        ),
        # Regular expenses
        SyntheticTransaction(
            merchant_name="Swiggy",
            merchant_category="5814",
            amount=1_500.0,
            direction="debit",
            days_ago=50,
        ),
        SyntheticTransaction(
            merchant_name="Netflix",
            merchant_category="7372",
            amount=649.0,
            direction="debit",
            days_ago=25,
        ),
    ],
)


# ---------------------------------------------------------------------------
# BUSINESS_CUSTOMER — startup founder with GST growth signals
# ---------------------------------------------------------------------------
BUSINESS_CUSTOMER = SyntheticCustomer(
    persona_type="startup_founder",
    risk_tier="low",
    salary_avg_3m=200_000.0,
    avg_balance_3m=800_000.0,
    total_investments=2_000_000.0,
    credit_score=780,
    transactions=[
        SyntheticTransaction(
            merchant_name="GSTN Payment",
            merchant_category="9311",
            amount=85_000.0,
            direction="debit",
            days_ago=10,
            notes="Q4 GST payment",
        ),
        SyntheticTransaction(
            merchant_name="ABC Vendor Payments",
            merchant_category="5065",
            amount=120_000.0,
            direction="debit",
            days_ago=8,
        ),
        SyntheticTransaction(
            merchant_name="XYZ Supplies",
            merchant_category="5065",
            amount=90_000.0,
            direction="debit",
            days_ago=5,
        ),
        SyntheticTransaction(
            merchant_name="Revenue Credit",
            merchant_category="6022",
            amount=300_000.0,
            direction="credit",
            days_ago=15,
        ),
    ],
)


# ---------------------------------------------------------------------------
# HIGH_RISK_CUSTOMER — missed EMI signals, low credit score
# ---------------------------------------------------------------------------
HIGH_RISK_CUSTOMER = SyntheticCustomer(
    persona_type="corporate_professional",
    risk_tier="high",
    salary_avg_3m=60_000.0,
    avg_balance_3m=30_000.0,
    total_investments=0.0,
    credit_score=610,
    transactions=[
        SyntheticTransaction(
            merchant_name="HDFC Home Loan EMI",
            merchant_category="6012",
            amount=22_000.0,
            direction="debit",
            days_ago=90,
        ),
        # Missed month — no EMI at day ~60
        SyntheticTransaction(
            merchant_name="HDFC Home Loan EMI",
            merchant_category="6012",
            amount=22_000.0,
            direction="debit",
            days_ago=30,
        ),
        SyntheticTransaction(
            merchant_name="Employer",
            merchant_category="6022",
            amount=60_000.0,
            direction="credit",
            days_ago=85,
        ),
        SyntheticTransaction(
            merchant_name="Employer",
            merchant_category="6022",
            amount=60_000.0,
            direction="credit",
            days_ago=30,
        ),
        # Jewellery spend for wedding event (should not generate outreach due to risk)
        SyntheticTransaction(
            merchant_name="Tanishq",
            merchant_category="5094",
            amount=30_000.0,
            direction="debit",
            days_ago=40,
        ),
    ],
)


# ---------------------------------------------------------------------------
# DB seeding helper
# ---------------------------------------------------------------------------
async def seed_to_db(db, synthetic: SyntheticCustomer) -> SyntheticCustomer:
    """
    Seed a SyntheticCustomer into the test database.
    Returns the synthetic object with customer_id populated.

    Requires an open AsyncSession. Used in integration tests.
    """
    from shared.db.models import (
        Customer,
        CustomerProfile,
        RelationshipManager,
        Transaction,
    )
    from shared.constants.enums import (
        KYCStatus, PersonaType, RiskTier, TransactionType,
    )

    customer_uuid = uuid.UUID(synthetic.customer_id)
    rm_uuid = uuid.UUID(synthetic.rm_id)

    # Create RM if not exists
    rm = RelationshipManager(
        id=rm_uuid,
        email=f"rm_{synthetic.rm_id[:8]}@testbank.com",
        hashed_password="$2b$12$dummyhashdummyhashdummyhashdummy",
        name="Test RM",
        branch_code="TEST001",
        is_active=True,
    )
    db.add(rm)

    # Create customer
    customer = Customer(
        id=customer_uuid,
        rm_id=rm_uuid,
        persona_type=PersonaType(synthetic.persona_type),
        risk_tier=RiskTier(synthetic.risk_tier),
        kyc_status=KYCStatus.FULL,
        relationship_tenure_months=synthetic.relationship_tenure_months,
    )
    db.add(customer)

    # Create profile
    profile = CustomerProfile(
        customer_id=customer_uuid,
        salary_avg_3m=synthetic.salary_avg_3m,
        avg_balance_3m=synthetic.avg_balance_3m,
        total_investments=synthetic.total_investments,
        total_liabilities=synthetic.total_liabilities,
        credit_score=synthetic.credit_score,
        product_holdings=synthetic.product_holdings,
        behavioral_tags=synthetic.behavioral_tags,
    )
    db.add(profile)

    # Create transactions
    now = datetime.now(timezone.utc)
    for txn in synthetic.transactions:
        from shared.constants.enums import TransactionDirection
        t = Transaction(
            customer_id=customer_uuid,
            amount=txn.amount,
            direction=TransactionDirection(txn.direction),
            txn_type=TransactionType.UPI,   # Default type for test data
            merchant_name=txn.merchant_name,
            merchant_category=txn.merchant_category,
            txn_at=now - timedelta(days=txn.days_ago),
            notes=txn.notes,
        )
        db.add(t)

    await db.commit()
    return synthetic
