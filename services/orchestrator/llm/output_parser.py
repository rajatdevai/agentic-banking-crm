# Structured output parser for LLM responses.
# Validates raw LLM JSON output against expected Pydantic schemas.
# If the LLM returns malformed JSON or a schema-invalid response,
# the parser raises a StructuredOutputError (not a generic exception)
# so the base agent can decide to retry or fall back.
# Hallucination guard: outputs referencing specific financial figures
# are flagged for human review rather than passed directly to the RM.

# TODO: implement OutputParser with Pydantic validation in Phase 5 (orchestrator layer)
