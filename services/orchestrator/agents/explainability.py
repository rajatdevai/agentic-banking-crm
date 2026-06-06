# Explainability Agent — generates RM-readable reasoning for each opportunity.
# This is one of two agents that uses an LLM (gpt-4o).
# Input: opportunity score, detected events + their evidence, product recommendation, risk flags.
# Output: a plain-English explanation card shown in the dashboard:
#   "[PERSON_1]: 87% conversion probability. Signals: jewellery spend (₹45K) + banquet
#    booking detected. No existing personal loan. CIBIL 762. Recommended: Personal Loan."
# PII check enforced by base class before LLM call — [PERSON_1] tokens only in prompt.

# TODO: implement ExplainabilityAgent with gpt-4o call in Phase 5 (orchestrator layer)
