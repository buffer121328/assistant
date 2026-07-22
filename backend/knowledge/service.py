"""Legacy service compatibility exports for :mod:`rag.service`.

New code should import from ``rag`` or ``rag.service``. This module keeps older
``knowledge.service`` imports working without retaining a second implementation.
"""

from rag.service import (
    MAX_IMPORT_BYTES,
    CHUNK_CHARS,
    CHUNK_OVERLAP,
    IngestionResult,
    KnowledgeDeleteResult,
    KnowledgeDocumentStatus,
    KnowledgeError,
    KnowledgeSearchResult,
    KnowledgeService,
)

__all__ = [
    "CHUNK_CHARS",
    "CHUNK_OVERLAP",
    "IngestionResult",
    "KnowledgeDeleteResult",
    "KnowledgeDocumentStatus",
    "KnowledgeError",
    "KnowledgeSearchResult",
    "KnowledgeService",
    "MAX_IMPORT_BYTES",
]
