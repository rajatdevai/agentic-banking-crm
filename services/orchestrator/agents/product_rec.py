# Product Recommendation Agent — maps (event_type, persona_type) to eligible products.
# Uses RAG to retrieve product eligibility rules from the knowledge base
# (personal_loan_eligibility.md, wealth_advisory_thresholds.md, etc.).
# Validates customer eligibility (CIBIL, salary, existing liabilities) against
# retrieved policy before adding a product to the recommendation list.
# One LLM call optional: to rank ambiguous product matches by fit score.

# TODO: implement ProductRecAgent with RAG retrieval in Phase 5 (orchestrator layer)
