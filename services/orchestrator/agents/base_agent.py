# Abstract base class enforced on all LangGraph agent nodes.
# Every agent must implement execute(state) → Partial[AgentState].
# The base class provides: exponential backoff retry (3 attempts), configurable
# timeout enforcement, structured output validation via Pydantic, automatic
# execution logging to agent_execution_logs, and a PII pre-flight assertion
# that verifies no raw PII exists in the prompt before any LLM call.

# TODO: implement BaseAgent abstract class in Phase 5 (orchestrator layer)
