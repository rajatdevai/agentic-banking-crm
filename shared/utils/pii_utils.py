# Shared PII detection utilities used by middleware and agent pre-flight checks.
# Wraps Microsoft Presidio Analyzer for entity detection.
# Provides: detect_pii(text) → list[PIIEntity], has_pii(text) → bool.
# Used in two places:
#   1. PIIMaskMiddleware (gateway): masks inbound request body before agent dispatch
#   2. BaseAgent PII pre-flight (orchestrator): asserts no raw PII in LLM prompt

# TODO: implement Presidio analyzer wrapper with banking-specific recognisers in Phase 3
