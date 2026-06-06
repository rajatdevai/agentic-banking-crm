"""
CustomerIntelAgent — pure DB read, no LLM call.

Reads: customer_id from state
Writes: customer_profile (CustomerProfile dataclass)

Queries customers + customer_profiles tables and assembles the typed CustomerProfile.
The external_cbs_id field is intentionally excluded from the dataclass.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select

from services.orchestrator.agents.base_agent import BaseAgent
from services.orchestrator.graph.state import AgentState
from shared.db.models import Customer, CustomerProfile as CustomerProfileORM
from shared.models.agent_state import CustomerProfile

logger = structlog.get_logger(__name__)


class CustomerIntelAgent(BaseAgent):
    agent_name = "CustomerIntelAgent"
    timeout_seconds = 10.0

    async def execute(self, state: AgentState) -> dict:
        customer_id = state["customer_id"]

        # Fetch customer row
        result = await self._db.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.deleted_at.is_(None),
            )
        )
        customer = result.scalar_one_or_none()
        if not customer:
            raise ValueError(f"Customer {customer_id} not found in database")

        # Fetch profile row (may not exist if not yet refreshed)
        prof_result = await self._db.execute(
            select(CustomerProfileORM).where(
                CustomerProfileORM.customer_id == customer_id
            )
        )
        profile_orm = prof_result.scalar_one_or_none()

        customer_profile = CustomerProfile(
            customer_id=str(customer.id),
            rm_id=str(customer.rm_id),
            persona_type=customer.persona_type,
            risk_tier=customer.risk_tier,
            kyc_status=customer.kyc_status,
            relationship_tenure_months=customer.relationship_tenure_months,
            salary_avg_3m=float(profile_orm.salary_avg_3m) if profile_orm and profile_orm.salary_avg_3m else None,
            avg_balance_3m=float(profile_orm.avg_balance_3m) if profile_orm and profile_orm.avg_balance_3m else None,
            total_investments=float(profile_orm.total_investments) if profile_orm and profile_orm.total_investments else None,
            total_liabilities=float(profile_orm.total_liabilities) if profile_orm and profile_orm.total_liabilities else None,
            credit_score=profile_orm.credit_score if profile_orm else None,
            product_holdings=profile_orm.product_holdings if profile_orm else {},
            behavioral_tags=profile_orm.behavioral_tags if profile_orm else [],
            last_refreshed_at=profile_orm.last_refreshed_at if profile_orm else None,
        )

        logger.info(
            "customer_intel_loaded",
            customer_id=customer_id,
            persona=customer_profile.persona_type,
            risk_tier=customer_profile.risk_tier,
        )

        return {"customer_profile": customer_profile}
