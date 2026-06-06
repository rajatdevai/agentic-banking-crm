# OpenAI LLM client. Single provider — no multi-provider fallback chain.
# Uses gpt-4o for quality-critical calls (explainability, outreach generation, copilot).
# Uses gpt-4o-mini for cost-optimized calls (summarisation, classification).
# All calls are async. Structured JSON output is enforced via response_format.
# Retry logic (3 attempts, exponential backoff) provided by tenacity decorator.
# Every call is logged: provider, model, token count, latency, masked input, output.

# TODO: implement OpenAI async client wrapper in Phase 5 (orchestrator layer)
