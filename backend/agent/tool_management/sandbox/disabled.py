from __future__ import annotations

from .types import SandboxResult


class DisabledSandboxRunner:
    def __init__(self, *, reason: str = "Sandbox provider is disabled") -> None:
        self.reason = reason

    @property
    def available(self) -> bool:
        return False

    async def execute(
        self,
        *,
        task_id: str,
        command: tuple[str, ...],
    ) -> SandboxResult:
        raise RuntimeError(self.reason)
