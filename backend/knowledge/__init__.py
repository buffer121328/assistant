"""User-authorized document ingestion and retrieval boundaries."""

from .extractors import PARSER_VERSION, SUPPORTED_MEDIA_TYPES, extract_text
from .service import (
    MAX_IMPORT_BYTES,
    IngestionResult,
    KnowledgeDocumentStatus,
    KnowledgeError,
    KnowledgeSearchResult,
    KnowledgeService,
)

__all__ = [
    "IngestionResult",
    "KnowledgeError",
    "KnowledgeDocumentStatus",
    "KnowledgeSearchResult",
    "KnowledgeService",
    "MAX_IMPORT_BYTES",
    "PARSER_VERSION",
    "SUPPORTED_MEDIA_TYPES",
    "extract_text",
]
