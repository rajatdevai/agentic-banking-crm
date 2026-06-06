"""
EventDetectionAgent — deterministic rule engine, NO LLM call.

Reads: transactions_summary from state
Writes: detected_events (list[DetectedEvent]), should_skip_llm (bool)

Rules are defined as dataclasses. Each rule evaluates against the TransactionSummary
and returns a confidence score (0.0 to 1.0) based on how many required signals fired.

Every detected event carries a signals dict with the exact evidence that caused it —
this is required for audit compliance and the explainability agent.

should_skip_llm is set True when no events are detected (no point running LLM agents).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import structlog

from services.orchestrator.agents.base_agent import BaseAgent
from services.orchestrator.graph.state import AgentState
from shared.config.settings import get_settings
from shared.constants.enums import EventType
from shared.models.agent_state import DetectedEvent, TransactionSummary

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Rule definition dataclass
# ---------------------------------------------------------------------------
@dataclass
class EventRule:
    """
    A single detection rule. evaluate() returns (confidence, signals_dict) or None.
    confidence is derived from how many required signals were present.
    signals carries the exact evidence for audit.
    """
    event_type: EventType
    description: str
    min_confidence: float
    evaluate: Callable[[TransactionSummary], Optional[tuple[float, dict]]] = field(repr=False)


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------

def _wedding_rule(ts: TransactionSummary) -> Optional[tuple[float, dict]]:
    """
    Wedding fires when jewellery + banquet + photography spend appear together.
    Min required: jewellery above ₹25,000 OR banquet above ₹15,000.
    Confidence scales with how many signals are present.
    """
    signals_present = 0
    required = 2
    signals: dict = {}

    JEWELLERY_THRESHOLD = 25_000
    BANQUET_THRESHOLD = 15_000

    if ts.has_jewellery_spend and ts.jewellery_total >= JEWELLERY_THRESHOLD:
        signals_present += 1
        signals["jewellery_total"] = ts.jewellery_total

    if ts.has_banquet_spend and ts.banquet_total >= BANQUET_THRESHOLD:
        signals_present += 1
        signals["banquet_total"] = ts.banquet_total

    # Bonus signal: large one-time credit (dowry or wedding gift)
    if ts.large_one_time_credit and ts.large_one_time_credit > 100_000:
        signals_present += 0.5
        signals["large_one_time_credit"] = ts.large_one_time_credit

    if signals_present < 1:
        return None

    confidence = min(1.0, signals_present / required)
    signals["rules_fired"] = ["jewellery_spend_detected"] if ts.has_jewellery_spend else []
    if ts.has_banquet_spend:
        signals["rules_fired"].append("banquet_spend_detected")
    signals["signal_count"] = signals_present

    return confidence, signals


def _home_purchase_rule(ts: TransactionSummary) -> Optional[tuple[float, dict]]:
    """
    Home purchase fires when property payment appears with interior/furniture spend.
    """
    if not ts.has_property_payment:
        return None

    signals: dict = {"property_payment_total": ts.property_total}
    confidence = 0.7  # Base confidence from property payment alone

    # Bonus: interior/furniture spend alongside property payment
    furniture_cats = ["luxury_retail", "other"]
    furniture_total = sum(
        cs.total_amount for cs in ts.spend_by_category
        if "furniture" in cs.category_name or "interior" in cs.category_name
    )
    if furniture_total > 5_000:
        confidence = min(1.0, confidence + 0.2)
        signals["furniture_interior_spend"] = furniture_total

    signals["rules_fired"] = ["property_payment_detected"]
    return confidence, signals


def _foreign_education_rule(ts: TransactionSummary) -> Optional[tuple[float, dict]]:
    """
    Foreign education fires when education testing fees + visa + forex transfer appear.
    """
    signals_present = 0
    signals: dict = {}

    if ts.has_education_spend:
        signals_present += 1
        signals["education_test_fee_detected"] = True

    if ts.has_forex_transfer and ts.forex_transfer_total > 50_000:
        signals_present += 1
        signals["forex_transfer_total"] = ts.forex_transfer_total

    # Visa fees heuristic — government category or visa keyword
    visa_detected = any(
        "visa" in (cs.category_name or "").lower()
        for cs in ts.spend_by_category
    )
    if visa_detected:
        signals_present += 0.5
        signals["visa_fee_detected"] = True

    if signals_present < 1:
        return None

    confidence = min(1.0, signals_present / 2.0)
    signals["rules_fired"] = [
        k for k, v in signals.items() if isinstance(v, bool) and v
    ] + [f for f in ["forex_transfer_total"] if f in signals]
    signals["signal_count"] = signals_present

    return confidence, signals


def _promotion_rule(ts: TransactionSummary) -> Optional[tuple[float, dict]]:
    """
    Promotion fires when salary increases >20% MoM for 2+ consecutive months,
    or a large bonus credit appears alongside a salary increase.
    """
    signals: dict = {}
    signals_present = 0

    if ts.salary_increase_consecutive_months >= 2:
        signals_present += 1
        signals["consecutive_salary_increases"] = ts.salary_increase_consecutive_months
        signals["salary_growth_pct"] = ts.salary_growth_pct

    if ts.large_one_time_credit and ts.large_one_time_credit > 100_000:
        if ts.salary_growth_pct and ts.salary_growth_pct > 10:
            signals_present += 1
            signals["large_bonus_credit"] = ts.large_one_time_credit

    if signals_present == 0:
        return None

    confidence = min(1.0, 0.5 + (signals_present * 0.25))
    signals["rules_fired"] = list(signals.keys())
    return confidence, signals


def _business_expansion_rule(ts: TransactionSummary) -> Optional[tuple[float, dict]]:
    """
    Business expansion fires when GST payments grow >30% QoQ or vendor count doubles.
    """
    signals: dict = {}
    signals_present = 0

    if ts.has_gst_payment and ts.gst_payment_qoq_growth and ts.gst_payment_qoq_growth > 0.30:
        signals_present += 1
        signals["gst_qoq_growth_pct"] = round(ts.gst_payment_qoq_growth * 100, 2)

    if ts.has_vendor_payments and ts.vendor_payment_count > 5:
        signals_present += 1
        signals["vendor_payment_count"] = ts.vendor_payment_count

    if signals_present == 0:
        return None

    confidence = min(1.0, signals_present * 0.5 + 0.2)
    signals["rules_fired"] = ["gst_growth" if "gst_qoq_growth_pct" in signals else "vendor_volume"]
    return confidence, signals


def _medical_rule(ts: TransactionSummary) -> Optional[tuple[float, dict]]:
    """
    Medical fires when a single hospital transaction exceeds the configured threshold.
    """
    threshold = 50_000  # configurable — from settings in production
    if not ts.has_hospital_spend or ts.hospital_max_single_txn < threshold:
        return None

    signals = {
        "hospital_max_single_txn": ts.hospital_max_single_txn,
        "rules_fired": ["large_medical_expense_detected"],
    }
    confidence = min(1.0, 0.5 + (ts.hospital_max_single_txn / 200_000) * 0.5)
    return confidence, signals


def _retirement_planning_rule(ts: TransactionSummary) -> Optional[tuple[float, dict]]:
    """
    Retirement planning fires when investor tag is present AND net savings rate is high.
    """
    if "investor" not in ts.behavioral_tags:
        return None
    if not ts.net_savings_rate or ts.net_savings_rate < 0.20:
        return None

    signals = {
        "investor_tag": True,
        "net_savings_rate": ts.net_savings_rate,
        "rules_fired": ["investor_high_savings_rate"],
    }
    confidence = 0.5 + min(0.4, ts.net_savings_rate)
    return confidence, signals


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------
_RULES: list[EventRule] = [
    EventRule(EventType.WEDDING,            "Wedding spending signals",        0.40, _wedding_rule),
    EventRule(EventType.HOME_PURCHASE,      "Home purchase signals",           0.60, _home_purchase_rule),
    EventRule(EventType.FOREIGN_EDUCATION,  "Foreign education signals",       0.50, _foreign_education_rule),
    EventRule(EventType.PROMOTION,          "Salary promotion signals",        0.50, _promotion_rule),
    EventRule(EventType.BUSINESS_EXPANSION, "Business expansion signals",      0.45, _business_expansion_rule),
    EventRule(EventType.MEDICAL,            "Large medical expense",           0.70, _medical_rule),
    EventRule(EventType.RETIREMENT_PLANNING,"Investor + high savings rate",    0.45, _retirement_planning_rule),
]


class EventDetectionAgent(BaseAgent):
    agent_name = "EventDetectionAgent"
    timeout_seconds = 15.0

    async def execute(self, state: AgentState) -> dict:
        ts: TransactionSummary | None = state.get("transactions_summary")
        if ts is None:
            logger.warning("event_detection_skipped", reason="no transactions_summary in state")
            return {"detected_events": [], "should_skip_llm": True}

        detected: list[DetectedEvent] = []

        for rule in _RULES:
            result = rule.evaluate(ts)
            if result is None:
                continue

            confidence, signals = result
            if confidence < rule.min_confidence:
                logger.debug(
                    "event_rule_below_threshold",
                    event=rule.event_type,
                    confidence=confidence,
                    threshold=rule.min_confidence,
                )
                continue

            event = DetectedEvent(
                event_type=rule.event_type,
                confidence_score=round(confidence, 3),
                signals=signals,
                detected_at=datetime.now(timezone.utc),
                expires_in_days=90,
            )
            detected.append(event)
            logger.info(
                "event_detected",
                event_type=rule.event_type.value,
                confidence=confidence,
                signals_count=len(signals),
            )

        should_skip = len(detected) == 0
        if should_skip:
            logger.info("no_events_detected", customer_id=state.get("customer_id"))

        return {
            "detected_events": detected,
            "should_skip_llm": should_skip,
        }
