from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .context import TaskContext
from .planner import ExecutionPlan


@dataclass(frozen=True)
class AgentRunInput:
    plan: ExecutionPlan
    context: TaskContext


ApprovalTypeName = Literal["tool", "plan", "review", "change"]


@dataclass(frozen=True)
class HumanApprovalRequest:
    approval_type: ApprovalTypeName
    subject: str
    summary: str
    tool_name: str | None = None


@dataclass(frozen=True)
class AgentRunResult:
    result_text: str
    display_plan: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    requested_tools: tuple[str, ...] = ()
    loop_steps: int = 1
    checkpoint_id: str | None = None
    approval_requests: tuple[HumanApprovalRequest, ...] = ()


class AgentExecutorProtocol(Protocol):
    async def execute(self, *, run_input: AgentRunInput) -> AgentRunResult: ...
