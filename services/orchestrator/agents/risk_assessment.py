"""
RiskAssessmentAgent — database-backed credit risk evaluation, no LLM call.

Reads: customer_profile, transactions_summary from state
Writes: risk_assessment (RiskAssessment dataclass)

Evaluates:
    - EMI-to-income ratio (total active EMI debits / monthly salary)
    - Delinquency signals (missed EMI pattern in last 6 months)
    - Balance trajectory (declining / stable / rising monthly average)
    - Credit score band
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import structlog
from sqlalchemy import and_, select

from services.orchestrator.agents.base_agent import BaseAgent
from services.orchestrator.graph.state import AgentState
from shared.constants.enums import RiskTier
from shared.db.models import CustomerProfile as CustomerProfileORM, Transaction
from shared.models.agent_state import CustomerProfile, RiskAssessment, TransactionSummary

logger = structlog.get_logger(__name__)

# EMI-related merchant keywords (heuristic detection)
_EMI_KEYWORDS = {"emi", "equated", "loan repayment", "hdfc loan", "sbi loan", "icici loan", "lic", "navi"}
_EMI_MCCS = {"6022", "6012", "9311"}  # Banking/finance MCCs


class RiskAssessmentAgent(BaseAgent):
    agent_name = "RiskAssessmentAgent"
    timeout_seconds = 15.0

    async def execute(self, state: AgentState) -> dict:
        cp: Optional[CustomerProfile] = state.get("customer_profile")
        ts: Optional[TransactionSummary] = state.get("transactions_summary")

        if cp is None:
            raise ValueError("RiskAssessmentAgent requires customer_profile in state")

        customer_id = cp.customer_id

        # Fetch fresh credit score from DB (may be more recent than profile snapshot)
        prof_result = await self._db.execute(
            select(CustomerProfileORM).where(
                CustomerProfileORM.customer_id == customer_id
            )
        )
        profile_orm = prof_result.scalar_one_or_none()
        credit_score = profile_orm.credit_score if profile_orm else cp.credit_score

        # Fetch last 6 months of debit transactions for EMI / delinquency analysis
        six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)
        txn_result = await self._db.execute(
            select(Transaction).where(
                and_(
                    Transaction.customer_id == customer_id,
                    Transaction.txn_at >= six_months_ago,
                    Transaction.direction == "debit",
                )
            ).order_by(Transaction.txn_at.asc())
        )
        debits = txn_result.scalars().all()

        # ----------------------------------------------------------------
        # 1. Identify EMI transactions
        # ----------------------------------------------------------------
        emi_txns = [
            t for t in debits
            if any(k in (t.merchant_name or "").lower() for k in _EMI_KEYWORDS)
            or t.merchant_category in _EMI_MCCS
        ]

        monthly_emi: dict[str, float] = defaultdict(float)
        for t in emi_txns:
            month_key = t.txn_at.strftime("%Y-%m")
            monthly_emi[month_key] += float(t.amount)

        avg_monthly_emi = sum(monthly_emi.values()) / max(len(monthly_emi), 1)
        salary = cp.salary_avg_3m or ts.salary_avg_3m if ts else None
        emi_to_income = (avg_monthly_emi / salary) if (salary and salary > 0) else None

        # ----------------------------------------------------------------
        # 2. Delinquency detection — missed EMI proxy
        # ----------------------------------------------------------------
        # A missed EMI is inferred when EMI-like transactions disappear for a month
        # while they were present in prior months
        emi_months = sorted(monthly_emi.keys())
        missed_emi_months = 0
        if len(emi_months) >= 2:
            for i in range(1, len(emi_months)):
                prev_emi = monthly_emi[emi_months[i - 1]]
                curr_emi = monthly_emi[emi_months[i]]
                # If EMI drops to <20% of previous month's amount, flag as missed
                if prev_emi > 0 and curr_emi < prev_emi * 0.20:
                    missed_emi_months += 1

        has_missed_emi = missed_emi_months > 0

        # ----------------------------------------------------------------
        # 3. Balance trajectory (monthly average balance trend)
        # ----------------------------------------------------------------
        monthly_debits: dict[str, float] = defaultdict(float)
        monthly_credits: dict[str, float] = defaultdict(float)
        for t in debits:
            monthly_debits[t.txn_at.strftime("%Y-%m")] += float(t.amount)

        all_txns_result = await self._db.execute(
            select(Transaction).where(
                and_(
                    Transaction.customer_id == customer_id,
                    Transaction.txn_at >= six_months_ago,
                    Transaction.direction == "credit",
                )
            )
        )
        credits_all = all_txns_result.scalars().all()
        for t in credits_all:
            monthly_credits[t.txn_at.strftime("%Y-%m")] += float(t.amount)

        all_months = sorted(set(list(monthly_debits.keys()) + list(monthly_credits.keys())))
        monthly_net = [
            monthly_credits.get(m, 0) - monthly_debits.get(m, 0)
            for m in all_months
        ]

        balance_trend = "stable"
        if len(monthly_net) >= 3:
            recent = monthly_net[-3:]
            if recent[-1] < recent[0] * 0.85:
                balance_trend = "declining"
            elif recent[-1] > recent[0] * 1.10:
                balance_trend = "rising"

        # ----------------------------------------------------------------
        # 4. Risk tier determination
        # ----------------------------------------------------------------
        risk_flags: dict = {}

        if credit_score is not None:
            if credit_score < 650:
                credit_band = "HIGH"
                risk_flags["credit_score_band"] = f"Poor ({credit_score})"
            elif credit_score < 750:
                credit_band = "MEDIUM"
            else:
                credit_band = "LOW"
        else:
            credit_band = "MEDIUM"  # Unknown defaults to medium

        # Override risk tier based on signals
        risk_tier = cp.risk_tier  # Start from DB value
        if credit_band == "HIGH" or (has_missed_emi and missed_emi_months >= 2):
            risk_tier = RiskTier.HIGH
            risk_flags["flag"] = "HIGH_RISK"
            if has_missed_emi:
                risk_flags["reason"] = f"Missed EMI detected in {missed_emi_months} month(s)"
        elif emi_to_income and emi_to_income > 0.40:
            risk_tier = RiskTier.MEDIUM
            risk_flags["flag"] = "MONITOR"
            risk_flags["emi_to_income_pct"] = round(emi_to_income * 100, 1)
        elif balance_trend == "declining":
            risk_flags["flag"] = "MONITOR"
            risk_flags["balance_trend"] = "declining"

        if credit_score:
            risk_flags["cibil"] = credit_score

        assessment = RiskAssessment(
            customer_id=customer_id,
            risk_tier=risk_tier,
            credit_score=credit_score,
            emi_to_income_ratio=round(emi_to_income, 3) if emi_to_income else None,
            balance_trend=balance_trend,
            has_missed_emi=has_missed_emi,
            missed_emi_months=missed_emi_months,
            loan_count=len(set(emi_months)),
            risk_flags=risk_flags,
        )

        logger.info(
            "risk_assessment_complete",
            customer_id=customer_id,
            risk_tier=risk_tier.value,
            credit_score=credit_score,
            has_missed_emi=has_missed_emi,
            balance_trend=balance_trend,
        )

        return {"risk_assessment": assessment}
