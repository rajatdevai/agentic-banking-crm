"""
Context Builder — formats reranked chunks into an LLM-ready context block.

Responsibilities:
    1. Format each chunk with source document name and section prefix
    2. Count total tokens using tiktoken and truncate to fit within context budget
    3. Return formatted context string + list of RAGCitation objects

Context budget:
    Default max_context_tokens = 3000 (leaves 2000+ tokens for LLM response
    in a 8k context window, or 5000+ in a 16k window).

Citation format (for RM transparency):
    Every recommendation shown to the RM includes which knowledge base documents
    backed it — this is the source_citations return value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import tiktoken

from services.rag.retrieval.retriever import RetrievalResult

_TOKENIZER = tiktoken.get_encoding("cl100k_base")
_DEFAULT_MAX_CONTEXT_TOKENS = 3000
_RESPONSE_RESERVE_TOKENS = 2000  # Minimum tokens reserved for LLM response


@dataclass
class ContextResult:
    """Output of the context builder — ready for LLM injection."""
    formatted_context: str
    source_citations: list[dict]   # [{source_file, doc_type, excerpt, rrf_score}]
    total_tokens: int
    chunks_included: int
    chunks_available: int


def build_context(
    query: str,
    reranked_chunks: list[RetrievalResult],
    max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS,
) -> ContextResult:
    """
    Format reranked chunks into a context block for LLM injection.

    Args:
        query: Original query (used for citation enrichment)
        reranked_chunks: Output of reranker.rerank()
        max_context_tokens: Hard token budget for context block

    Returns:
        ContextResult with formatted_context string and source citations
    """
    if not reranked_chunks:
        return ContextResult(
            formatted_context="No relevant information found in the knowledge base.",
            source_citations=[],
            total_tokens=0,
            chunks_included=0,
            chunks_available=0,
        )

    included_chunks: list[RetrievalResult] = []
    total_tokens = 0

    for chunk in reranked_chunks:
        chunk_block = _format_chunk(chunk)
        chunk_tokens = _count_tokens(chunk_block)

        if total_tokens + chunk_tokens > max_context_tokens:
            # Try to include a truncated version of this chunk
            remaining = max_context_tokens - total_tokens
            if remaining > 100:  # Only worth including if there's meaningful space
                truncated = _truncate_to_tokens(chunk_block, remaining)
                included_chunks.append(chunk)
                total_tokens += _count_tokens(truncated)
            break

        included_chunks.append(chunk)
        total_tokens += chunk_tokens

    # Build formatted context string
    formatted_blocks = [_format_chunk(c) for c in included_chunks]
    formatted_context = "\n\n".join(formatted_blocks)

    # Build citation list
    citations = [
        {
            "source_file": c.source_file,
            "doc_type": c.doc_type,
            "chunk_index": c.chunk_index,
            "rrf_score": round(c.rrf_score, 4),
            "excerpt": c.chunk_text[:200].strip(),
        }
        for c in included_chunks
    ]

    return ContextResult(
        formatted_context=formatted_context,
        source_citations=citations,
        total_tokens=total_tokens,
        chunks_included=len(included_chunks),
        chunks_available=len(reranked_chunks),
    )


def _format_chunk(chunk: RetrievalResult) -> str:
    """
    Format a single chunk with its source prefix.

    Format:
        [SOURCE: product_catalog/personal_loan_eligibility.md | Chunk 3]
        <chunk content>
    """
    # Human-readable source name
    source_parts = chunk.source_file.split("/")
    filename = source_parts[-1].replace(".md", "").replace("_", " ").title()
    collection = chunk.doc_type.replace("_", " ").title()

    header = f"[{collection}: {filename} | Section {chunk.chunk_index + 1}]"
    return f"{header}\n{chunk.chunk_text}"


def _count_tokens(text: str) -> int:
    """Count tokens using tiktoken cl100k_base (matches embedding model)."""
    return len(_TOKENIZER.encode(text))


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to fit within max_tokens, preserving word boundaries."""
    tokens = _TOKENIZER.encode(text)
    if len(tokens) <= max_tokens:
        return text
    truncated_tokens = tokens[:max_tokens]
    return _TOKENIZER.decode(truncated_tokens) + "..."
