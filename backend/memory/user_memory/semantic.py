from __future__ import annotations

from typing import Any

from domain.models import Memory, Task


class SemanticMemorySyncMixin:
    """Synchronize MemoryService changes with optional semantic memory."""

    async def _semantic_add(self: Any, *, task: Task, memory: Memory) -> bool:
        """执行 处理 semantic add 的内部辅助逻辑。

        Args:
            task: task 参数。
            memory: memory 参数。
        """
        if not self.semantic_memory.enabled:
            return False
        try:
            return await self.semantic_memory.add(
                user_id=task.user_id,
                run_id=task.id,
                memory_id=memory.id,
                content=memory.content,
            )
        except Exception:
            return False

    async def _semantic_delete(self: Any, *, user_id: str, memory_id: str) -> bool:
        """执行 处理 semantic delete 的内部辅助逻辑。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        if not self.semantic_memory.enabled:
            return False
        try:
            return await self.semantic_memory.delete(
                user_id=user_id,
                memory_id=memory_id,
            )
        except Exception:
            return False
