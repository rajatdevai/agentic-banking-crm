"""
Embedder — generates vector embeddings using OpenAI text-embedding-3-large.

Key design decisions:
    - Batch size: 50 chunks per API call (OpenAI limit is 2048; 50 is safe and fast)
    - Embedding dimension: 1536 (Matryoshka truncation of 3072 native dims)
      text-embedding-3-large supports native truncation via the 'dimensions' parameter
    - Rate limit handling: tenacity exponential backoff on RateLimitError
    - Returns: list of (chunk, embedding_vector) pairs

Matryoshka Representation Learning (MRL):
    text-embedding-3-large was trained with MRL, which means the first N dimensions
    of the full embedding carry the most semantic information. Truncating to 1536
    dimensions reduces storage and HNSW index size by 50% with minimal quality loss
    (per OpenAI benchmarks: ~1% MTEB score reduction vs full 3072 dims).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from openai import AsyncOpenAI, RateLimitError, APIConnectionError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from services.rag.ingestion.chunker import DocumentChunk
from shared.config.settings import get_settings

logger = structlog.get_logger(__name__)

_BATCH_SIZE = 50
_EMBEDDING_MODEL = "text-embedding-3-large"
_EMBEDDING_DIMENSIONS = 1536   # Matryoshka truncated from 3072


async def embed_chunks(
    chunks: list[DocumentChunk],
) -> list[tuple[DocumentChunk, list[float]]]:
    """
    Generate embeddings for all chunks in batches of 50.

    Args:
        chunks: Output of chunker.chunk_documents()

    Returns:
        List of (DocumentChunk, embedding_vector) pairs, same order as input.
        embedding_vector is a list of 1536 floats (normalised, unit-length).
    """
    settings = get_settings()
    if settings.OPENAI_API_KEY.startswith("sk-...") or not settings.OPENAI_API_KEY:
        results: list[tuple[DocumentChunk, list[float]]] = []
        for chunk in chunks:
            results.append((chunk, [0.0] * _EMBEDDING_DIMENSIONS))
        logger.info("embedding_complete_dummy", total_chunks=len(results))
        return results

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=60.0)

    results: list[tuple[DocumentChunk, list[float]]] = []
    total_batches = (len(chunks) + _BATCH_SIZE - 1) // _BATCH_SIZE

    for batch_idx in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[batch_idx: batch_idx + _BATCH_SIZE]
        batch_num = batch_idx // _BATCH_SIZE + 1

        logger.info(
            "embedding_batch",
            batch=batch_num,
            total_batches=total_batches,
            chunks_in_batch=len(batch),
        )

        embeddings = await _embed_batch_with_retry(client, batch)
        for chunk, embedding in zip(batch, embeddings):
            results.append((chunk, embedding))

    logger.info("embedding_complete", total_chunks=len(results))
    return results


async def _embed_batch_with_retry(
    client: AsyncOpenAI,
    batch: list[DocumentChunk],
) -> list[list[float]]:
    """
    Embed a single batch with tenacity retry on rate limits and connection errors.
    Returns list of 1536-dim vectors in the same order as the input batch.
    """
    texts = [chunk.chunk_text for chunk in batch]

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        retry=retry_if_exception_type((RateLimitError, APIConnectionError, TimeoutError)),
        reraise=True,
    ):
        with attempt:
            response = await client.embeddings.create(
                input=texts,
                model=_EMBEDDING_MODEL,
                dimensions=_EMBEDDING_DIMENSIONS,  # Native Matryoshka truncation
            )

    # Extract and return embedding vectors in order
    # OpenAI guarantees response.data is ordered the same as input
    return [item.embedding for item in response.data]


def embed_query_sync(query: str) -> list[float]:
    """
    Synchronous wrapper for single-query embedding (used in retriever).
    Creates a new event loop if called from a non-async context.
    """
    return asyncio.run(_embed_single_query(query))


async def embed_query(query: str) -> list[float]:
    """
    Async version of single-query embedding for use in async retriever.
    """
    return await _embed_single_query(query)


async def _embed_single_query(query: str) -> list[float]:
    """Embed a single query string for retrieval."""
    settings = get_settings()
    if settings.OPENAI_API_KEY.startswith("sk-...") or not settings.OPENAI_API_KEY:
        return [0.0] * 1536

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=30.0)

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
        reraise=True,
    ):
        with attempt:
            response = await client.embeddings.create(
                input=[query],
                model=_EMBEDDING_MODEL,
                dimensions=_EMBEDDING_DIMENSIONS,
            )

    return response.data[0].embedding
