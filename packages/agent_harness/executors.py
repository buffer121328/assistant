from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .context import TaskContext
from .planner import ExecutionPlan


@dataclass(frozen=True)
class AgentRunInput:
    plan: ExecutionPlan
    context: TaskContext


@dataclass(frozen=True)
class AgentRunResult:
    result_text: str
    display_plan: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    requested_tools: tuple[str, ...] = ()
    loop_steps: int = 1
    checkpoint_id: str | None = None


class AgentExecutorProtocol(Protocol):
    async def execute(self, *, run_input: AgentRunInput) -> AgentRunResult: ...
