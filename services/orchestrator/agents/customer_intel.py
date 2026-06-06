# Customer Intelligence Agent — first node in the LangGraph DAG.
# Reads customer_ids from AgentState, queries PostgreSQL for full customer profiles
# (persona_type, risk_tier, product_holdings, behavioral_tags, relationship_tenure),
# and writes a structured CustomerProfile back to AgentState.
# No LLM calls in this agent — pure database reads.

# TODO: implement CustomerIntelAgent in Phase 5 (orchestrator layer)
