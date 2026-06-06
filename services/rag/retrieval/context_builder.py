# Context builder — assembles the final RAG context window for the LLM prompt.
# Takes top-5 reranked chunks, formats them with source attribution,
# trims to fit within the agent's token budget, and returns a structured context string.
# Context includes: chunk text, document type, source name, and effective date.

# TODO: implement context window assembly with token budget management in Phase 6 (RAG layer)
