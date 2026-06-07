import pytest
from services.orchestrator.agents.event_detection import _wealth_migration_rule
from shared.models.agent_state import TransactionSummary

def test_wealth_migration_rule_ignores_no_forex():
    ts = TransactionSummary(
        customer_id="test-customer",
        has_forex_transfer=False,
        forex_transfer_total=0.0
    )
    result = _wealth_migration_rule(ts)
    assert result is None

def test_wealth_migration_rule_below_threshold():
    ts = TransactionSummary(
        customer_id="test-customer",
        has_forex_transfer=True,
        forex_transfer_total=199999.0
    )
    result = _wealth_migration_rule(ts)
    assert result is None

def test_wealth_migration_rule_above_threshold():
    ts = TransactionSummary(
        customer_id="test-customer",
        has_forex_transfer=True,
        forex_transfer_total=250000.0
    )
    result = _wealth_migration_rule(ts)
    assert result is not None
    confidence, signals = result
    assert confidence == 0.8
    assert signals["forex_transfer_total"] == 250000.0
    assert "large_forex_transfer_detected" in signals["rules_fired"]

def test_wealth_migration_rule_extreme_value():
    ts = TransactionSummary(
        customer_id="test-customer",
        has_forex_transfer=True,
        forex_transfer_total=1500000.0
    )
    result = _wealth_migration_rule(ts)
    assert result is not None
    confidence, signals = result
    assert confidence == 1.0
    assert signals["forex_transfer_total"] == 1500000.0
