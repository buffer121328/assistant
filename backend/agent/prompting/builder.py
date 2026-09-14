from __future__ import annotations

from typing import Any

from .store import PromptStore
from .types import PromptBuildResult


class PromptBuilder:
    """定义当前组件的职责和边界。"""

    def __init__(self, store: PromptStore) -> None:
        """Initialize the builder with a prompt store.

        Args:
            store: 用于执行当前操作的 store 参数。
        """
        self.store = store

    def build(self, runtime_context: dict[str, Any] | None = None) -> PromptBuildResult:
        """Build a prompt for a runtime context.

        Args:
            runtime_context: 用于执行当前操作的 runtime context 参数。
        """
        return self.store.build(runtime_context)
