# RM Copilot Agent — conversational agent handling ad-hoc RM questions.
# Uses gpt-4o with RAG-backed context: retrieves relevant customer profiles,
# outreach history, product catalog, and persona playbooks per query.
# Maintains conversation history within the session for follow-up questions.
# Streams token-by-token via SSE to the gateway's /copilot/chat endpoint.
# Also assembles the final structured response when the full pipeline completes.

# TODO: implement RMCopilotAgent with streaming + RAG in Phase 5 (orchestrator layer)
