from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent.planning.context import TaskContext
from agent.planning.profiles import AgentProfile, ExecutionMode, ExecutorKind


MAX_PLAN_STEPS = 12
MAX_PLAN_TIMEOUT_SECONDS = 300.0
MAX_PLAN_TOOLS = 15


@dataclass(frozen=True)
class ExecutionPlan:
    """表示 处理 execution plan 的后端数据结构或服务对象。"""

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
    execution_mode: ExecutionMode = "react"
    require_plan_approval: bool = False
    max_review_retries: int = 0
    max_replans: int = 0
    max_subagents: int = 0
    subagent_concurrency: int = 1
    subagent_timeout_seconds: float = 30.0


class PlanningLayerProtocol(Protocol):
    """表示 处理 planning layer protocol 的后端数据结构或服务对象。"""

    def build_plan(
        self,
        *,
        task: Any,
        profile: AgentProfile,
        context: TaskContext,
    ) -> ExecutionPlan:
        """构建 plan。

        Args:
            task: task 参数。
            profile: profile 参数。
            context: context 参数。
        """
        ...


class DefaultPlanningLayer:
    """表示 处理 default planning layer 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        max_steps: int = MAX_PLAN_STEPS,
        max_timeout_seconds: float = MAX_PLAN_TIMEOUT_SECONDS,
        max_tool_count: int = MAX_PLAN_TOOLS,
    ) -> None:
        """初始化对象实例。

        Args:
            max_steps: max_steps 参数。
            max_timeout_seconds: max_timeout_seconds 参数。
            max_tool_count: max_tool_count 参数。
        """
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
        """构建 plan。

        Args:
            task: task 参数。
            profile: profile 参数。
            context: context 参数。
        """
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
            execution_mode=profile.execution_mode,
            require_plan_approval=profile.require_plan_approval,
            max_review_retries=max(0, min(profile.max_review_retries, 2)),
            max_replans=max(0, min(profile.max_replans, 2)),
            max_subagents=max(0, min(profile.max_subagents, 3)),
            subagent_concurrency=max(1, min(profile.subagent_concurrency, 3)),
            subagent_timeout_seconds=max(
                1.0,
                min(profile.subagent_timeout_seconds, 60.0),
            ),
        )


def _plan_goal(input_text: str, task_type: str) -> str:
    """执行 处理 plan goal 的内部辅助逻辑。

    Args:
        input_text: input_text 参数。
        task_type: task_type 参数。
    """
    command = f"/{task_type}"
    if input_text.startswith(command):
        goal = input_text.removeprefix(command).strip()
        if goal:
            return goal
    return input_text.strip()
