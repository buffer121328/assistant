"""Legacy extractor compatibility exports for :mod:`rag.extractors`."""

from rag.extractors import (
    PARSER_VERSION,
    SUPPORTED_MEDIA_TYPES,
    ExtractionError,
    OptionalOfficeDependencyError,
    extract_text,
)

__all__ = [
    "ExtractionError",
    "OptionalOfficeDependencyError",
    "PARSER_VERSION",
    "SUPPORTED_MEDIA_TYPES",
    "extract_text",
]
