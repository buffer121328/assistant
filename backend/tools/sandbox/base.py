from __future__ import annotations

from typing import Protocol

from .types import SandboxResult


class SandboxRunner(Protocol):
    """表示 处理 sandbox runner 的后端数据结构或服务对象。"""

    @property
    def available(self) -> bool:
        """处理 available。"""
        ...

    async def execute(
        self,
        *,
        task_id: str,
        command: tuple[str, ...],
    ) -> SandboxResult:
        """执行。

        Args:
            task_id: task_id 参数。
            command: command 参数。
        """
        ...
