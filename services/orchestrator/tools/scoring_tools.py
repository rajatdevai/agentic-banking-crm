# ML model scoring tool functions called by the Opportunity Scoring Agent.
# Wraps HTTP calls to the internal ML model server (services/ml/serving/model_server.py).
# Functions: get_conversion_score(features), get_churn_score(features), get_clv(features).
# Falls back to heuristic rules if model server is unavailable (circuit breaker pattern).

# TODO: implement scoring tool functions in Phase 7 (ML layer)
