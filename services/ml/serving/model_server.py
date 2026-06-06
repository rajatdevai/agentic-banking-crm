# FastAPI ML model serving endpoint (internal service — not public).
# Exposes: POST /score/conversion, POST /score/churn, POST /score/clv
# Loads model artifacts at startup. Validates feature input against schema.
# Returns scores with feature importance breakdown for explainability.
# Used by orchestrator/tools/scoring_tools.py via internal HTTP call.

# TODO: implement ML model server with FastAPI endpoints in Phase 7 (ML layer)
