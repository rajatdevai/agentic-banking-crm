# Transaction Intelligence Agent — runs in parallel with Event Detection Agent.
# Queries TimescaleDB for the customer's 90-day transaction history.
# Aggregates spend patterns: avg monthly spend by MCC category, income credits,
# EMI deductions, and derives behavioral tags (travel_heavy, investor, luxury_spender).
# No LLM calls — pure SQL aggregation and rule-based tagging.

# TODO: implement TransactionIntelAgent in Phase 5 (orchestrator layer)
