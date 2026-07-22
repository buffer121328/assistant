"""Shared cross-cutting helpers with no application-layer dependencies."""

from .redaction import sanitize_text

__all__ = ["sanitize_text"]
