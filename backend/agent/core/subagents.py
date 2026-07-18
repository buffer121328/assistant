from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Protocol


@dataclass(frozen=True)
class SubAgentRequest:
    step_index: int
    role: str
    objective: str
    context: str
    task_id: str = ""
    user_id: str = ""


@dataclass(frozen=True)
class SubAgentResult:
    step_index: int
    role: str
    content: str
    error: str | None = None


class SubAgentRunner(Protocol):
    async def run(self, request: SubAgentRequest) -> SubAgentResult: ...


class SubAgentCoordinator:
    def __init__(
        self,
        *,
        runner: SubAgentRunner,
        max_subagents: int = 3,
        concurrency: int = 2,
        timeout_seconds: float = 30.0,
        max_result_chars: int = 10_000,
    ) -> None:
        self.runner = runner
        self.max_subagents = max(0, min(max_subagents, 3))
        self.concurrency = max(1, min(concurrency, 3))
        self.timeout_seconds = max(1.0, min(timeout_seconds, 60.0))
        self.max_result_chars = max(500, min(max_result_chars, 20_000))

    async def run(
        self,
        *,
        task_id: str,
        user_id: str,
        requests: tuple[SubAgentRequest, ...],
    ) -> tuple[SubAgentResult, ...]:
        selected = requests[: self.max_subagents]
        semaphore = asyncio.Semaphore(self.concurrency)

        async def execute(request: SubAgentRequest) -> SubAgentResult:
            scoped = replace(
                request,
                task_id=task_id,
                user_id=user_id,
                context=request.context[:20_000],
            )
            try:
                async with semaphore:
                    result = await asyncio.wait_for(
                        self.runner.run(scoped),
                        timeout=self.timeout_seconds,
                    )
            except Exception as exc:
                return SubAgentResult(
                    step_index=scoped.step_index,
                    role=scoped.role[:64],
                    content="",
                    error=type(exc).__name__,
                )
            return SubAgentResult(
                step_index=scoped.step_index,
                role=result.role[:64],
                content=result.content[: self.max_result_chars],
                error=(result.error[:200] if result.error else None),
            )

        results = await asyncio.gather(*(execute(item) for item in selected))
        return tuple(sorted(results, key=lambda item: item.step_index))
