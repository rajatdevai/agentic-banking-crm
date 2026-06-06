# Redis-backed LangGraph checkpointer.
# Persists AgentState to Redis at each graph node boundary so that failed runs
# can resume from the last successful checkpoint rather than restarting from scratch.
# TTL per checkpoint is tied to session lifetime (default: 8 hours).

# TODO: implement Redis checkpointer wrapping LangGraph's BaseCheckpointSaver in Phase 5
