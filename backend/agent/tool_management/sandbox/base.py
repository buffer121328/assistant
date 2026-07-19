from __future__ import annotations

from typing import Protocol

from .types import SandboxResult


class SandboxRunner(Protocol):
    @property
    def available(self) -> bool: ...

    async def execute(
        self,
        *,
        task_id: str,
        command: tuple[str, ...],
    ) -> SandboxResult: ...
