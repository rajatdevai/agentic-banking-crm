"""
Paragraph-boundary aware chunker using tiktoken for token counting.

Strategy:
    1. Split document on double newlines (paragraph boundaries)
    2. Merge small paragraphs greedily until chunk reaches 800-1000 tokens
    3. Never split in the middle of a paragraph unless the paragraph itself
       exceeds max_tokens (then split at sentence boundary)
    4. Each chunk carries all parent document metadata + chunk_index

Why not fixed-size character splitting?
    Fixed splitting cuts across sentences, destroys semantic coherence, and
    produces poor embeddings. Paragraph-aware chunking preserves meaning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import tiktoken

from services.rag.ingestion.loader import LoadedDocument

# Target token range for each chunk
_MIN_CHUNK_TOKENS = 200
_TARGET_CHUNK_TOKENS = 800
_MAX_CHUNK_TOKENS = 1000

# Sentence boundary pattern for fallback splitting of oversized paragraphs
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

# Use cl100k_base encoding (same as text-embedding-3-large)
_TOKENIZER = tiktoken.get_encoding("cl100k_base")


@dataclass
class DocumentChunk:
    """
    A single chunk ready for embedding and indexing.
    Carries all parent document metadata for retrieval attribution.
    """
    doc_type: str
    source_file: str
    chunk_index: int
    chunk_text: str
    token_count: int
    version: str
    effective_date: Optional[str]

    # Derived metadata
    section_hint: str = ""   # Nearest heading above this chunk (for context)


def chunk_documents(
    documents: list[LoadedDocument],
    target_tokens: int = _TARGET_CHUNK_TOKENS,
    max_tokens: int = _MAX_CHUNK_TOKENS,
) -> list[DocumentChunk]:
    """
    Chunk all loaded documents into embedding-ready pieces.

    Args:
        documents: Output of loader.load_knowledge_base()
        target_tokens: Target chunk size in tokens (800 default)
        max_tokens: Hard max — chunks exceeding this are split further

    Returns:
        Flat list of DocumentChunk objects, ordered by (source_file, chunk_index)
    """
    all_chunks: list[DocumentChunk] = []

    for doc in documents:
        chunks = _chunk_document(doc, target_tokens=target_tokens, max_tokens=max_tokens)
        all_chunks.extend(chunks)

    total_tokens = sum(c.token_count for c in all_chunks)
    return all_chunks


def _chunk_document(
    doc: LoadedDocument,
    target_tokens: int,
    max_tokens: int,
) -> list[DocumentChunk]:
    """Chunk a single document into semantic paragraphs."""
    paragraphs = _split_into_paragraphs(doc.raw_text)

    # Merge small paragraphs into target-sized chunks
    merged_chunks: list[str] = []
    current_chunk: list[str] = []
    current_tokens = 0
    current_section = ""

    for para in paragraphs:
        # Track nearest heading for section_hint
        if para.startswith("#"):
            current_section = para.split("\n")[0].lstrip("#").strip()

        para_tokens = _count_tokens(para)

        # Paragraph alone exceeds max — split at sentence boundary
        if para_tokens > max_tokens:
            # Flush current buffer first
            if current_chunk:
                merged_chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_tokens = 0
            # Split the oversized paragraph at sentence boundaries
            sentence_chunks = _split_at_sentences(para, max_tokens)
            merged_chunks.extend(sentence_chunks)
            continue

        # Adding this paragraph would overflow — flush and start new chunk
        if current_tokens + para_tokens > target_tokens and current_chunk:
            merged_chunks.append("\n\n".join(current_chunk))
            current_chunk = []
            current_tokens = 0

        current_chunk.append(para)
        current_tokens += para_tokens

    # Flush remaining
    if current_chunk:
        merged_chunks.append("\n\n".join(current_chunk))

    # Build DocumentChunk objects
    chunks: list[DocumentChunk] = []
    for idx, text in enumerate(merged_chunks):
        text = text.strip()
        if not text:
            continue
        token_count = _count_tokens(text)
        chunks.append(DocumentChunk(
            doc_type=doc.doc_type,
            source_file=doc.source_file,
            chunk_index=idx,
            chunk_text=text,
            token_count=token_count,
            version=doc.version,
            effective_date=doc.effective_date,
            section_hint=current_section,
        ))

    return chunks


def _split_into_paragraphs(text: str) -> list[str]:
    """
    Split on double newlines. Strip whitespace from each paragraph.
    Skip empty paragraphs and horizontal rules.
    """
    raw_paragraphs = re.split(r"\n{2,}", text)
    paragraphs = []
    for para in raw_paragraphs:
        stripped = para.strip()
        if not stripped:
            continue
        # Skip horizontal rules
        if re.match(r"^[-*_]{3,}$", stripped):
            continue
        paragraphs.append(stripped)
    return paragraphs


def _split_at_sentences(text: str, max_tokens: int) -> list[str]:
    """Split text at sentence boundaries to fit within max_tokens."""
    sentences = _SENTENCE_BOUNDARY.split(text)
    result: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sent_tokens = _count_tokens(sentence)
        if current_tokens + sent_tokens > max_tokens and current:
            result.append(" ".join(current))
            current = []
            current_tokens = 0
        current.append(sentence)
        current_tokens += sent_tokens

    if current:
        result.append(" ".join(current))

    return result


def _count_tokens(text: str) -> int:
    """Count tokens using the cl100k_base tokeniser (matches embedding model)."""
    return len(_TOKENIZER.encode(text))
