from __future__ import annotations

from .types import SandboxResult


class DisabledSandboxRunner:
    """表示 处理 disabled sandbox runner 的后端数据结构或服务对象。"""

    def __init__(self, *, reason: str = "Sandbox provider is disabled") -> None:
        """初始化对象实例。

        Args:
            reason: reason 参数。
        """
        self.reason = reason

    @property
    def available(self) -> bool:
        """处理 available。"""
        return False

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
        raise RuntimeError(self.reason)
