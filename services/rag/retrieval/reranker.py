# Cross-encoder reranker — second stage of RAG retrieval.
# Takes top-30 candidates from hybrid retriever and reranks to top-5.
# Primary: Cohere Rerank API (hosted, high quality).
# Fallback: local bge-reranker-v2-m3 model (runs in-process, no external dependency).
# Reranking significantly improves precision for domain-specific financial queries.

# TODO: implement cross-encoder reranker with Cohere + local fallback in Phase 6 (RAG layer)
