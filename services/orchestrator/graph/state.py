# AgentState TypedDict — the single shared state object passed through the LangGraph DAG.
# Every agent reads from this state and writes only its own output fields back.
# No agent calls another agent directly; all coordination happens through state mutations.
# This is the backbone of the entire orchestration system.

# TODO: implement full AgentState TypedDict in Phase 5 (orchestrator layer)
