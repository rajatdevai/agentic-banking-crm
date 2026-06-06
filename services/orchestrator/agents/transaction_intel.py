"""
TransactionIntelAgent — 90-day transaction analysis, no LLM call.

Reads: customer_id from state
Writes: transactions_summary (TransactionSummary dataclass)

Computes statistical signals from raw transactions:
    - Spend by MCC category (grouped and ranked)
    - Salary credit pattern (consistency, growth)
    - Behavioral tags (travel_heavy, luxury_spender, investor, business_operator)
    - Event-relevant signals (jewellery, banquet, education, forex, medical, property, GST)

MCC category mappings used:
    5094 = Jewellery / Precious Stones
    7011, 7922, 7999 = Banquets / Events / Entertainment
    7011 hotels, 4511 airlines = Travel
    5912, 5999 = Luxury retail
    8299, 8243 = Education testing (IELTS, GRE, TOEFL)
    4215, 4814 = Forex / International wire
    8062 = Hospital / Medical
    6552, 1520 = Real estate / Property
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import and_, func, select

from services.orchestrator.agents.base_agent import BaseAgent
from services.orchestrator.graph.state import AgentState
from shared.db.models import Transaction
from shared.models.agent_state import CategorySpend, TransactionSummary

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# MCC → category name mapping (abbreviated — full mapping in a reference table)
# ---------------------------------------------------------------------------
_MCC_CATEGORIES: dict[str, str] = {
    "5094": "jewellery",
    "5944": "jewellery",
    "7011": "hospitality",
    "7922": "events_entertainment",
    "7999": "events_entertainment",
    "4511": "travel_airlines",
    "4411": "travel_cruise",
    "7512": "travel_car_rental",
    "5912": "luxury_retail",
    "5999": "luxury_retail",
    "5945": "luxury_retail",
    "8299": "education_testing",
    "8249": "education_testing",
    "8243": "education_testing",
    "4215": "international_transfer",
    "4814": "international_transfer",
    "8062": "medical_hospital",
    "8011": "medical_hospital",
    "8099": "medical_hospital",
    "6552": "real_estate",
    "1520": "real_estate",
    "7389": "business_services",
    "5065": "vendor_payments",
    "9311": "gst_tax",
    "7372": "software_saas",
    "5411": "grocery",
    "5814": "food_dining",
    "5541": "fuel",
    "6022": "banking_transfer",
}

# Merchant name keywords for fallback detection
_JEWELLERY_KEYWORDS = {"tanishq", "senco", "joyalukkas", "kalyan", "malabar"}
_BANQUET_KEYWORDS = {"banquet", "catering", "event space", "wedding hall", "mandap"}
_EDUCATION_KEYWORDS = {"ielts", "gre", "toefl", "sat", "coaching", "edtech", "coursera"}
_HOSPITAL_KEYWORDS = {"hospital", "clinic", "nursing home", "healthcare", "apollo", "fortis"}
_REAL_ESTATE_KEYWORDS = {"property", "realty", "housing", "dlf", "godrej properties", "sobha"}
_GST_KEYWORDS = {"gst", "tax payment", "gstn", "income tax", "advance tax"}
_VENDOR_KEYWORDS = {"vendor", "supplier", "wholesale", "b2b"}

SALARY_MCC_BYPASS = {"6022", "9999"}  # Bank credits often have no MCC


class TransactionIntelAgent(BaseAgent):
    agent_name = "TransactionIntelAgent"
    timeout_seconds = 20.0

    async def execute(self, state: AgentState) -> dict:
        customer_id = state["customer_id"]
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)

        # Fetch last 90 days of transactions
        result = await self._db.execute(
            select(Transaction).where(
                and_(
                    Transaction.customer_id == customer_id,
                    Transaction.txn_at >= cutoff,
                )
            ).order_by(Transaction.txn_at.asc())
        )
        transactions = result.scalars().all()

        summary = self._analyse(customer_id=customer_id, transactions=transactions)

        logger.info(
            "transaction_intel_complete",
            customer_id=customer_id,
            txn_count=len(transactions),
            tags=summary.behavioral_tags,
        )
        return {"transactions_summary": summary}

    def _analyse(self, customer_id: str, transactions: list[Transaction]) -> TransactionSummary:
        summary = TransactionSummary(customer_id=customer_id)
        if not transactions:
            return summary

        # ----------------------------------------------------------------
        # 1. Totals
        # ----------------------------------------------------------------
        debits = [t for t in transactions if t.direction.value == "debit"]
        credits = [t for t in transactions if t.direction.value == "credit"]
        summary.total_debit_90d = sum(float(t.amount) for t in debits)
        summary.total_credit_90d = sum(float(t.amount) for t in credits)

        # ----------------------------------------------------------------
        # 2. Salary detection — large recurring credits from banking channel
        # ----------------------------------------------------------------
        monthly_credits: dict[str, list[float]] = defaultdict(list)
        for txn in credits:
            month_key = txn.txn_at.strftime("%Y-%m")
            amount = float(txn.amount)
            # Heuristic: salary = largest credit in a month above 10k that recurs
            if amount > 10_000:
                monthly_credits[month_key].append(amount)

        monthly_max = {month: max(amounts) for month, amounts in monthly_credits.items()}
        if len(monthly_max) >= 2:
            vals = list(monthly_max.values())
            summary.salary_avg_3m = sum(vals) / len(vals)
            if len(vals) >= 2:
                growth = (vals[-1] - vals[-2]) / vals[-2] if vals[-2] > 0 else 0
                summary.salary_growth_pct = round(growth * 100, 2)
                if vals[-1] > vals[-2] * 1.20:
                    summary.salary_increase_consecutive_months += 1

        # Large one-time credit (likely bonus)
        single_credits = sorted(
            [float(t.amount) for t in credits if float(t.amount) > 50_000], reverse=True
        )
        if single_credits:
            summary.bonus_credits = single_credits[:3]
            summary.large_one_time_credit = single_credits[0]

        # ----------------------------------------------------------------
        # 3. Spend by MCC category
        # ----------------------------------------------------------------
        cat_amounts: dict[str, float] = defaultdict(float)
        cat_counts: dict[str, int] = defaultdict(int)

        for txn in debits:
            mcc = txn.merchant_category or "unknown"
            cat = _MCC_CATEGORIES.get(mcc, "other")

            # Keyword fallback for uncategorised transactions
            merchant_lower = (txn.merchant_name or "").lower()
            if cat == "other":
                if any(k in merchant_lower for k in _JEWELLERY_KEYWORDS):
                    cat = "jewellery"
                elif any(k in merchant_lower for k in _BANQUET_KEYWORDS):
                    cat = "events_entertainment"
                elif any(k in merchant_lower for k in _EDUCATION_KEYWORDS):
                    cat = "education_testing"
                elif any(k in merchant_lower for k in _HOSPITAL_KEYWORDS):
                    cat = "medical_hospital"
                elif any(k in merchant_lower for k in _REAL_ESTATE_KEYWORDS):
                    cat = "real_estate"
                elif any(k in merchant_lower for k in _GST_KEYWORDS):
                    cat = "gst_tax"
                elif any(k in merchant_lower for k in _VENDOR_KEYWORDS):
                    cat = "vendor_payments"

            cat_amounts[cat] += float(txn.amount)
            cat_counts[cat] += 1

        total_spend = summary.total_debit_90d or 1.0
        spend_list: list[CategorySpend] = []
        for cat, amount in sorted(cat_amounts.items(), key=lambda x: x[1], reverse=True):
            count = cat_counts[cat]
            spend_list.append(CategorySpend(
                mcc_code=cat,
                category_name=cat,
                total_amount=amount,
                transaction_count=count,
                avg_transaction=amount / count if count > 0 else 0,
                pct_of_total_spend=round(amount / total_spend * 100, 2),
            ))
        summary.spend_by_category = spend_list

        # ----------------------------------------------------------------
        # 4. Event signals extraction
        # ----------------------------------------------------------------
        jewellery_total = cat_amounts.get("jewellery", 0.0)
        if jewellery_total > 0:
            summary.has_jewellery_spend = True
            summary.jewellery_total = jewellery_total

        banquet_total = cat_amounts.get("events_entertainment", 0.0)
        if banquet_total > 0:
            summary.has_banquet_spend = True
            summary.banquet_total = banquet_total

        travel_total = sum(cat_amounts.get(c, 0) for c in ["travel_airlines", "travel_cruise", "travel_car_rental"])
        if travel_total > 0:
            summary.has_travel_spend = True
            summary.travel_total = travel_total

        luxury_total = cat_amounts.get("luxury_retail", 0.0)
        if luxury_total > 0:
            summary.has_luxury_spend = True
            summary.luxury_total = luxury_total

        education_total = cat_amounts.get("education_testing", 0.0)
        if education_total > 0:
            summary.has_education_spend = True

        intl_total = cat_amounts.get("international_transfer", 0.0)
        if intl_total > 0:
            summary.has_forex_transfer = True
            summary.forex_transfer_total = intl_total

        hospital_txns = [t for t in debits
                         if _MCC_CATEGORIES.get(t.merchant_category or "", "other") == "medical_hospital"
                         or any(k in (t.merchant_name or "").lower() for k in _HOSPITAL_KEYWORDS)]
        if hospital_txns:
            summary.has_hospital_spend = True
            summary.hospital_max_single_txn = max(float(t.amount) for t in hospital_txns)

        property_total = cat_amounts.get("real_estate", 0.0)
        if property_total > 0:
            summary.has_property_payment = True
            summary.property_total = property_total

        gst_total = cat_amounts.get("gst_tax", 0.0)
        if gst_total > 0:
            summary.has_gst_payment = True

        vendor_count = cat_counts.get("vendor_payments", 0)
        if vendor_count > 0:
            summary.has_vendor_payments = True
            summary.vendor_payment_count = vendor_count

        # ----------------------------------------------------------------
        # 5. Behavioral tags
        # ----------------------------------------------------------------
        tags: list[str] = []
        if travel_total / total_spend > 0.15:
            tags.append("travel_heavy")
        if luxury_total / total_spend > 0.10:
            tags.append("luxury_spender")
        if summary.has_gst_payment or summary.has_vendor_payments:
            tags.append("business_operator")
        # Investor tag: recurring transfers to investment platforms (heuristic: brokerage keywords)
        investment_keywords = {"zerodha", "groww", "kuvera", "mutual fund", "sip", "nse", "bse"}
        if any(any(k in (t.merchant_name or "").lower() for k in investment_keywords) for t in debits):
            tags.append("investor")

        summary.behavioral_tags = tags

        # ----------------------------------------------------------------
        # 6. Savings rate
        # ----------------------------------------------------------------
        if summary.total_credit_90d > 0:
            summary.net_savings_rate = round(
                (summary.total_credit_90d - summary.total_debit_90d) / summary.total_credit_90d,
                3,
            )

        return summary
