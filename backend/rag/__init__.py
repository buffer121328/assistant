"""Stable RAG facade for knowledge ingestion, retrieval, citations, and deletion.

The legacy ``knowledge`` package remains import-compatible while callers migrate to
this production-facing boundary one module at a time.
"""

from .citations import (
    CitableSource,
    CitationValidationResult,
    citation_token,
    format_retrieval_context,
    validate_citation_references,
)
from .extractors import PARSER_VERSION, SUPPORTED_MEDIA_TYPES, extract_text
from .service import (
    MAX_IMPORT_BYTES,
    IngestionResult,
    KnowledgeDeleteResult,
    KnowledgeDocumentStatus,
    KnowledgeError,
    KnowledgeSearchResult,
    KnowledgeService,
)

__all__ = [
    "CitableSource",
    "CitationValidationResult",
    "IngestionResult",
    "KnowledgeDeleteResult",
    "KnowledgeDocumentStatus",
    "KnowledgeError",
    "KnowledgeSearchResult",
    "KnowledgeService",
    "MAX_IMPORT_BYTES",
    "PARSER_VERSION",
    "SUPPORTED_MEDIA_TYPES",
    "citation_token",
    "extract_text",
    "format_retrieval_context",
    "validate_citation_references",
]
