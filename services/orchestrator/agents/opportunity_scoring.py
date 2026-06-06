# Opportunity Scoring Agent — combines customer profile + detected events + risk flags
# to produce a composite priority score per customer.
# Calls the ML model server (XGBoost) for conversion_probability.
# Calls the CLV model for revenue_potential estimation.
# Writes a ranked list of Opportunity objects to AgentState.
# No LLM calls — pure ML inference and score composition.

# TODO: implement OpportunityScoringAgent in Phase 5 (orchestrator layer)
