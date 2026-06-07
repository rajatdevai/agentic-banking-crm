"""
Typed dataclass contracts shared between all agents.

These dataclasses are the inter-agent API. If CustomerIntelAgent writes a
CustomerProfile, every downstream agent can rely on its exact fields and types.
Never pass raw dicts between agents — always use these typed dataclasses.

Design principles:
- All fields have explicit types (no Any)
- Defaults for optional fields use field(default_factory=...) — never mutable defaults
- No business logic lives here — pure data containers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from shared.constants.enums import (
    EventType,
    KYCStatus,
    OpportunityStatus,
    OutreachChannel,
    PersonaType,
    ProductType,
    RiskTier,
)


# ---------------------------------------------------------------------------
# CustomerProfile — written by CustomerIntelAgent
# ---------------------------------------------------------------------------
@dataclass
class CustomerProfile:
    """
    Full customer snapshot assembled from customers + customer_profiles tables.
    external_cbs_id is intentionally EXCLUDED — this dataclass is used in agent
    prompts (after masking) so CBS IDs must never appear here.
    """
    customer_id: str
    rm_id: str
    persona_type: PersonaType
    risk_tier: RiskTier
    kyc_status: KYCStatus
    relationship_tenure_months: int

    # Financial snapshot from customer_profiles
    salary_avg_3m: Optional[float] = None
    avg_balance_3m: Optional[float] = None
    total_investments: Optional[float] = None
    total_liabilities: Optional[float] = None
    credit_score: Optional[int] = None

    # Enriched attributes
    product_holdings: dict = field(default_factory=dict)
    behavioral_tags: list[str] = field(default_factory=list)
    last_refreshed_at: Optional[datetime] = None

    def salary_band(self) -> str:
        """Return human-readable salary band for prompts."""
        if self.salary_avg_3m is None:
            return "unknown"
        s = self.salary_avg_3m
        if s < 30_000:
            return "sub-30k"
        elif s < 75_000:
            return "30k-75k"
        elif s < 150_000:
            return "75k-1.5L"
        elif s < 500_000:
            return "1.5L-5L"
        else:
            return "5L+"

    def holds_product(self, product: ProductType) -> bool:
        """Check if customer already holds a product (from product_holdings JSONB)."""
        return bool(self.product_holdings.get(product.value, False))

    def debt_to_income_ratio(self) -> Optional[float]:
        """Approximate DTI — total liabilities / (salary * 12)."""
        if self.total_liabilities and self.salary_avg_3m and self.salary_avg_3m > 0:
            return self.total_liabilities / (self.salary_avg_3m * 12)
        return None


# ---------------------------------------------------------------------------
# TransactionSummary — written by TransactionIntelAgent
# ---------------------------------------------------------------------------
@dataclass
class CategorySpend:
    """Spend summary for a single MCC category."""
    mcc_code: str
    category_name: str
    total_amount: float
    transaction_count: int
    avg_transaction: float
    pct_of_total_spend: float


@dataclass
class TransactionSummary:
    """
    90-day transaction analysis produced by TransactionIntelAgent.
    Pure statistical summary — no raw transaction data, no PII.
    """
    customer_id: str
    analysis_window_days: int = 90

    # Salary / income analysis
    salary_avg_3m: Optional[float] = None
    salary_growth_pct: Optional[float] = None        # MoM growth over last 2 months
    bonus_credits: list[float] = field(default_factory=list)  # One-time large credits
    net_savings_rate: Optional[float] = None         # (income - spend) / income

    # Spend breakdown by category
    spend_by_category: list[CategorySpend] = field(default_factory=list)
    total_debit_90d: float = 0.0
    total_credit_90d: float = 0.0

    # Behavioral signals (used by EventDetectionAgent rules)
    has_jewellery_spend: bool = False
    jewellery_total: float = 0.0
    has_banquet_spend: bool = False
    banquet_total: float = 0.0
    has_travel_spend: bool = False
    travel_total: float = 0.0
    has_luxury_spend: bool = False
    luxury_total: float = 0.0
    has_education_spend: bool = False     # IELTS/GRE/TOEFL payments
    has_visa_spend: bool = False
    has_forex_transfer: bool = False
    forex_transfer_total: float = 0.0
    has_hospital_spend: bool = False
    hospital_max_single_txn: float = 0.0
    has_property_payment: bool = False
    property_total: float = 0.0
    has_gst_payment: bool = False
    gst_payment_qoq_growth: Optional[float] = None
    has_vendor_payments: bool = False
    vendor_payment_count: int = 0
    salary_increase_consecutive_months: int = 0     # Consecutive months of >20% salary increase
    large_one_time_credit: Optional[float] = None   # Largest single non-salary credit

    # Derived behavioral tags
    behavioral_tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# DetectedEvent — written by EventDetectionAgent
# ---------------------------------------------------------------------------
@dataclass
class DetectedEvent:
    """
    A life event inferred by the deterministic rule engine.
    confidence_score is derived from how many required signals were present.
    signals carries the exact evidence for audit and explainability.
    """
    event_type: EventType
    confidence_score: float              # 0.0 to 1.0
    signals: dict                        # Exact evidence dict — must be audit-ready
    detected_at: datetime = field(default_factory=datetime.utcnow)
    expires_in_days: int = 90            # How long this event window stays valid
    db_event_id: Optional[str] = None   # Set after persisting to DB


# ---------------------------------------------------------------------------
# RiskAssessment — written by RiskAssessmentAgent
# ---------------------------------------------------------------------------
@dataclass
class RiskAssessment:
    """Customer risk evaluation produced by RiskAssessmentAgent."""
    customer_id: str
    risk_tier: RiskTier
    credit_score: Optional[int] = None

    # Risk signals
    emi_to_income_ratio: Optional[float] = None       # EMI outflows / salary
    balance_trend: str = "stable"                     # rising | stable | declining
    has_missed_emi: bool = False
    missed_emi_months: int = 0
    loan_count: int = 0

    # Structured risk flags for opportunity display
    risk_flags: dict = field(default_factory=dict)
    # Example: {"flag": "MONITOR", "reason": "1 missed EMI 4 months ago", "cibil": 691}

    def is_eligible_for_unsecured_loan(self) -> bool:
        """Quick eligibility gate for personal loan / working capital."""
        if self.risk_tier == RiskTier.HIGH:
            return False
        if self.has_missed_emi and self.missed_emi_months > 2:
            return False
        if self.emi_to_income_ratio and self.emi_to_income_ratio > 0.5:
            return False
        return True


# ---------------------------------------------------------------------------
# Opportunity — written by OpportunityScoringAgent
# ---------------------------------------------------------------------------
@dataclass
class Opportunity:
    """A scored, ranked opportunity to present to the RM."""
    customer_id: str
    event_type: EventType
    product_recommended: ProductType
    priority_score: float               # Composite ranking metric
    conversion_probability: float       # 0.0 to 1.0 from XGBoost or heuristic
    revenue_potential: Optional[float] = None
    risk_flags: dict = field(default_factory=dict)
    scoring_method: str = "xgboost"    # "xgboost" | "heuristic_fallback"
    db_opportunity_id: Optional[str] = None


# ---------------------------------------------------------------------------
# ProductRecommendation — written by ProductRecAgent
# ---------------------------------------------------------------------------
@dataclass
class RAGCitation:
    """Source citation from the RAG knowledge base."""
    chunk_id: str
    doc_type: str
    relevance_score: float
    excerpt: str                        # First 200 chars of the chunk


@dataclass
class ProductRecommendation:
    """
    A product recommendation with RAG-sourced eligibility evidence.
    citations shows exactly which knowledge base chunks were used — full auditability.
    """
    product_type: ProductType
    event_type: EventType
    eligibility_rationale: str
    citations: list[RAGCitation] = field(default_factory=list)
    persona_specific_notes: str = ""


# ---------------------------------------------------------------------------
# OutreachMessage — written by OutreachGenAgent
# ---------------------------------------------------------------------------
@dataclass
class OutreachMessage:
    """
    A generated outreach message ready for RM review.
    pii_safe guarantees the message_body contains no raw PII — only natural
    placeholders if names are needed (e.g., "Dear Valued Customer").
    """
    channel: OutreachChannel
    message_body: str
    persona_tone: str
    pii_safe: bool = True               # Validated by OutreachGenAgent before writing
    db_campaign_id: Optional[str] = None
    opportunity_id: Optional[str] = None
    product_type: Optional[ProductType] = None
    option_a: Optional[str] = None
    option_b: Optional[str] = None
