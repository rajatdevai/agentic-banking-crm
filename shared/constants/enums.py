"""
Shared domain enumerations for the RM Copilot platform.
Single source of truth for all enum values used across services, agents, and DB models.
All enums inherit from (str, Enum) for clean JSON serialization and PostgreSQL ENUM compatibility.
"""

from enum import Enum


class PersonaType(str, Enum):
    """Customer persona classification — drives engagement strategy and tone selection."""
    CORPORATE_PROFESSIONAL = "corporate_professional"
    YOUNG_IT_PROFESSIONAL = "young_it_professional"
    STARTUP_FOUNDER = "startup_founder"
    DOCTOR = "doctor"
    LAWYER = "lawyer"
    HNI = "hni"
    AFFLUENT_INVESTOR = "affluent_investor"
    BUSINESS_OWNER = "business_owner"
    NRI_FAMILY = "nri_family"
    NEWLY_MARRIED = "newly_married"
    PRE_RETIREMENT = "pre_retirement"


class EventType(str, Enum):
    """Life events detectable from transaction pattern analysis via the rule engine."""
    WEDDING = "wedding"
    HOME_PURCHASE = "home_purchase"
    FOREIGN_EDUCATION = "foreign_education"
    CHILD_EDUCATION = "child_education"
    MEDICAL = "medical"
    BUSINESS_EXPANSION = "business_expansion"
    PROMOTION = "promotion"
    WEALTH_MIGRATION = "wealth_migration"
    RETIREMENT_PLANNING = "retirement_planning"
    NEW_BORN = "new_born"


class ProductType(str, Enum):
    """Banking products that can be recommended by the opportunity scoring pipeline."""
    PERSONAL_LOAN = "personal_loan"
    HOME_LOAN = "home_loan"
    EDUCATION_LOAN = "education_loan"
    WORKING_CAPITAL_LOAN = "working_capital_loan"
    LOAN_AGAINST_SECURITIES = "loan_against_securities"
    GOLD_LOAN = "gold_loan"
    WEALTH_ADVISORY = "wealth_advisory"
    MUTUAL_FUND = "mutual_fund"
    FIXED_DEPOSIT = "fixed_deposit"
    FOREX_CARD = "forex_card"
    CURRENT_ACCOUNT = "current_account"
    HEALTH_INSURANCE = "health_insurance"
    CHILD_EDUCATION_PLAN = "child_education_plan"
    PREMIUM_CREDIT_CARD = "premium_credit_card"
    INSURANCE = "insurance"
    BUSINESS_CREDIT_CARD = "business_credit_card"


class RiskTier(str, Enum):
    """Credit risk classification — determines eligible products and loan limits."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OpportunityStatus(str, Enum):
    """Lifecycle state of an opportunity from detection to conversion or dismissal."""
    NEW = "new"
    RM_VIEWED = "rm_viewed"
    OUTREACH_SENT = "outreach_sent"
    CONVERTED = "converted"
    DISMISSED = "dismissed"


class OutreachChannel(str, Enum):
    """Communication channel used for customer outreach — all require RM approval before dispatch."""
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class TransactionType(str, Enum):
    """Payment rail / instrument type for a transaction."""
    UPI = "upi"
    CARD = "card"
    NEFT = "neft"
    IMPS = "imps"
    ATM = "atm"


class TransactionDirection(str, Enum):
    """Whether the transaction is a debit (outflow) or credit (inflow) from the customer's account."""
    DEBIT = "debit"
    CREDIT = "credit"


class KYCStatus(str, Enum):
    """Customer KYC verification state — expired KYC blocks product recommendations."""
    FULL = "full"
    COMPLETE = "complete"
    PENDING = "pending"
    EXPIRED = "expired"
