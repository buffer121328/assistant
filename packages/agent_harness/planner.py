from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .context import TaskContext
from .profiles import AgentProfile, ExecutorKind


MAX_PLAN_STEPS = 12
MAX_PLAN_TIMEOUT_SECONDS = 300.0
MAX_PLAN_TOOLS = 15


@dataclass(frozen=True)
class ExecutionPlan:
    goal: str
    steps: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    approval_required_tools: tuple[str, ...]
    max_steps: int
    timeout_seconds: float
    risk_level: str
    output_format: str
    profile_name: str
    executor_kind: ExecutorKind
    workflow_key: str
    tool_snapshot_revision: int = 0
    tool_count_budget: int = MAX_PLAN_TOOLS
    tool_versions: tuple[tuple[str, str], ...] = ()


class PlanningLayerProtocol(Protocol):
    def build_plan(
        self,
        *,
        task: Any,
        profile: AgentProfile,
        context: TaskContext,
    ) -> ExecutionPlan: ...


class DefaultPlanningLayer:
    def __init__(
        self,
        *,
        max_steps: int = MAX_PLAN_STEPS,
        max_timeout_seconds: float = MAX_PLAN_TIMEOUT_SECONDS,
        max_tool_count: int = MAX_PLAN_TOOLS,
    ) -> None:
        self.max_steps = max(1, min(max_steps, MAX_PLAN_STEPS))
        self.max_timeout_seconds = max(
            1.0,
            min(max_timeout_seconds, MAX_PLAN_TIMEOUT_SECONDS),
        )
        self.max_tool_count = max(0, min(max_tool_count, MAX_PLAN_TOOLS))

    def build_plan(
        self,
        *,
        task: Any,
        profile: AgentProfile,
        context: TaskContext,
    ) -> ExecutionPlan:
        steps = profile.default_steps or ("读取任务上下文", "完成输出")
        allowed_tools = context.allowed_tools[: self.max_tool_count]
        remaining = max(0, self.max_tool_count - len(allowed_tools))
        approval_required_tools = context.approval_required_tools[:remaining]
        selected_names = set((*allowed_tools, *approval_required_tools))
        return ExecutionPlan(
            goal=_plan_goal(str(task.input_text), str(task.task_type)),
            steps=steps[: self.max_steps],
            allowed_tools=allowed_tools,
            approval_required_tools=approval_required_tools,
            max_steps=max(1, min(profile.max_steps, self.max_steps)),
            timeout_seconds=max(
                1.0,
                min(profile.timeout_seconds, self.max_timeout_seconds),
            ),
            risk_level=profile.risk_level,
            output_format=profile.output_format,
            profile_name=profile.name,
            executor_kind=profile.executor_kind,
            workflow_key=profile.workflow_key,
            tool_snapshot_revision=(
                context.tool_snapshot_revision or context.capability_revision
            ),
            tool_count_budget=self.max_tool_count,
            tool_versions=tuple(
                (name, version)
                for name, version in context.tool_versions
                if name in selected_names
            ),
        )


def _plan_goal(input_text: str, task_type: str) -> str:
    command = f"/{task_type}"
    if input_text.startswith(command):
        goal = input_text.removeprefix(command).strip()
        if goal:
            return goal
    return input_text.strip()
