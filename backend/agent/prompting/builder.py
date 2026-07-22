from __future__ import annotations

from typing import Any

from .store import PromptStore
from .types import PromptBuildResult


class PromptBuilder:
    """Small facade for building prompts from a PromptStore."""

    def __init__(self, store: PromptStore) -> None:
        """Initialize the builder with a prompt store."""
        self.store = store

    def build(self, runtime_context: dict[str, Any] | None = None) -> PromptBuildResult:
        """Build a prompt for a runtime context."""
        return self.store.build(runtime_context)
