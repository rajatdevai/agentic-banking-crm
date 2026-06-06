# Database tool functions used by agent nodes.
# Provides async SQLAlchemy query helpers for: customer profile reads,
# transaction history aggregation, opportunity reads/writes,
# detected_events inserts, and outreach_campaigns records.
# All functions accept masked customer tokens — never raw PII.

# TODO: implement DB tool functions in Phase 5 (orchestrator layer)
