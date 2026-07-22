"""Legacy compatibility exports for the primary :mod:`rag` package.

New first-party code should import from ``rag``. This package remains so older
callers using ``knowledge`` keep working during the V12 directory migration.
"""

from rag import (
    MAX_IMPORT_BYTES,
    PARSER_VERSION,
    SUPPORTED_MEDIA_TYPES,
    IngestionResult,
    KnowledgeDeleteResult,
    KnowledgeDocumentStatus,
    KnowledgeError,
    KnowledgeSearchResult,
    KnowledgeService,
    extract_text,
)

__all__ = [
    "IngestionResult",
    "KnowledgeDeleteResult",
    "KnowledgeDocumentStatus",
    "KnowledgeError",
    "KnowledgeSearchResult",
    "KnowledgeService",
    "MAX_IMPORT_BYTES",
    "PARSER_VERSION",
    "SUPPORTED_MEDIA_TYPES",
    "extract_text",
]
