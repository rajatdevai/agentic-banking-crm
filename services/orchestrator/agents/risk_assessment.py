# Risk Assessment Agent — evaluates credit risk before any product recommendation is finalised.
# Reads credit_score, existing_liabilities, delinquency_history from customer profile.
# Applies internal credit policy rules (retrieved from RAG: internal_credit_policy.md).
# Sets risk_flags on each opportunity (DECLINED, HIGH_RISK, MONITOR, CLEAR).
# No LLM calls — deterministic rule application against credit policy thresholds.

# TODO: implement RiskAssessmentAgent in Phase 5 (orchestrator layer)
