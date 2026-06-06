# Document loader for the RAG ingestion pipeline.
# Supports: PDF (pdfplumber), DOCX (python-docx), Markdown (.md), CSV, and plain text.
# Extracts raw text with metadata: source path, document type, version, effective date.
# Does NOT perform chunking — that is handled by chunker.py.

# TODO: implement multi-format document loader in Phase 6 (RAG layer)
