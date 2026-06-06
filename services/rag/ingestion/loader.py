"""
Document Loader — reads markdown files from the knowledge_base directory.

Extracts raw text and attaches metadata:
    - doc_type: derived from subdirectory name (product_catalog, policy_docs, etc.)
    - source_file: relative path from knowledge_base root
    - version: from file front matter (## Version: ...) or file mtime
    - effective_date: from file front matter (## Effective Date: ...)
    - raw_text: full document content

Returns a list of LoadedDocument dataclasses ready for chunking.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# Map subdirectory names to doc_type labels
_DIR_TO_DOC_TYPE: dict[str, str] = {
    "product_catalog":   "product_catalog",
    "policy_docs":       "policy_docs",
    "persona_playbooks": "persona_playbooks",
    "market_context":    "market_context",
}

# Patterns for extracting front matter from markdown
_VERSION_PATTERN = re.compile(
    r"^\*\*Version[:\*]+\*+\s*(.+)$|^Version[:\s]+(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_EFFECTIVE_DATE_PATTERN = re.compile(
    r"^\*\*Effective Date[:\*]+\*+\s*(.+)$|^Effective Date[:\s]+(.+)$",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass
class LoadedDocument:
    """A single document loaded from the knowledge base, ready for chunking."""
    doc_type: str
    source_file: str       # Relative path: e.g. "product_catalog/personal_loan_eligibility.md"
    raw_text: str
    version: str
    effective_date: Optional[str]
    file_mtime: datetime


def load_knowledge_base(knowledge_base_dir: str | Path) -> list[LoadedDocument]:
    """
    Walk the knowledge_base directory tree and load all markdown files.

    Args:
        knowledge_base_dir: Absolute path to the knowledge_base/ directory

    Returns:
        List of LoadedDocument objects, one per markdown file

    Raises:
        FileNotFoundError: if knowledge_base_dir does not exist
    """
    kb_path = Path(knowledge_base_dir).resolve()
    if not kb_path.exists():
        raise FileNotFoundError(f"Knowledge base directory not found: {kb_path}")

    documents: list[LoadedDocument] = []

    for subdir_name, doc_type in _DIR_TO_DOC_TYPE.items():
        subdir = kb_path / subdir_name
        if not subdir.exists():
            logger.warning("knowledge_base_subdir_missing", subdir=str(subdir))
            continue

        md_files = sorted(subdir.glob("*.md"))
        logger.info("loading_docs", doc_type=doc_type, count=len(md_files))

        for md_file in md_files:
            try:
                doc = _load_single_file(md_file, doc_type, kb_path)
                documents.append(doc)
                logger.debug("document_loaded", source=doc.source_file, chars=len(doc.raw_text))
            except Exception as exc:
                logger.error("document_load_failed", file=str(md_file), error=str(exc))

    logger.info("knowledge_base_loaded", total_documents=len(documents))
    return documents


def _load_single_file(
    md_file: Path,
    doc_type: str,
    kb_root: Path,
) -> LoadedDocument:
    """Load a single markdown file and extract its metadata."""
    raw_text = md_file.read_text(encoding="utf-8")
    source_file = str(md_file.relative_to(kb_root)).replace("\\", "/")

    # File modification time as fallback version
    mtime_ts = md_file.stat().st_mtime
    file_mtime = datetime.fromtimestamp(mtime_ts, tz=timezone.utc)

    # Extract version from front matter
    version = _extract_version(raw_text, file_mtime)

    # Extract effective date from front matter
    effective_date = _extract_effective_date(raw_text)

    return LoadedDocument(
        doc_type=doc_type,
        source_file=source_file,
        raw_text=raw_text,
        version=version,
        effective_date=effective_date,
        file_mtime=file_mtime,
    )


def _extract_version(text: str, fallback_mtime: datetime) -> str:
    """Extract version string from markdown front matter or fall back to file date."""
    match = _VERSION_PATTERN.search(text)
    if match:
        version_str = (match.group(1) or match.group(2) or "").strip()
        # Clean up markdown bold syntax
        version_str = re.sub(r"\*+", "", version_str).strip()
        if version_str:
            return version_str
    return fallback_mtime.strftime("%Y-%m-%d")


def _extract_effective_date(text: str) -> Optional[str]:
    """Extract effective date from markdown front matter."""
    match = _EFFECTIVE_DATE_PATTERN.search(text)
    if match:
        date_str = (match.group(1) or match.group(2) or "").strip()
        date_str = re.sub(r"\*+", "", date_str).strip()
        if date_str:
            return date_str
    return None
