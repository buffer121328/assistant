from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from features import feature_for_task_type, planning_task_types
from models import SUPPORTED_MODEL_CLASSES


ExecutorKind = Literal["langgraph"]
RiskLevel = Literal["low", "medium", "high"]
ExecutionMode = Literal["react", "plan_execute_review"]

SUPPORTED_PLANNING_TASK_TYPES = planning_task_types()


class UnsupportedWorkflowTaskTypeError(Exception):
    """表示 处理 unsupported workflow task type error 的后端数据结构或服务对象。"""

    pass


class UnsupportedModelClassError(Exception):
    """表示 处理 unsupported model class error 的后端数据结构或服务对象。"""

    pass


@dataclass(frozen=True)
class AgentProfile:
    """表示 处理 agent profile 的后端数据结构或服务对象。"""

    name: str
    executor_kind: ExecutorKind
    workflow_key: str
    skill_names: tuple[str, ...] = ()
    requested_tools: tuple[str, ...] = ()
    default_steps: tuple[str, ...] = ()
    max_steps: int = 3
    timeout_seconds: float = 60.0
    risk_level: RiskLevel = "low"
    output_format: str = "markdown"
    execution_mode: ExecutionMode = "react"
    require_plan_approval: bool = False
    max_review_retries: int = 0
    max_replans: int = 0
    max_subagents: int = 0
    subagent_concurrency: int = 1
    subagent_timeout_seconds: float = 30.0


class DefaultProfileSelector:
    """表示 处理 default profile selector 的后端数据结构或服务对象。"""

    def select(self, task: Any) -> AgentProfile:
        """选择。

        Args:
            task: task 参数。
        """
        task_type = str(task.task_type).strip()
        feature = feature_for_task_type(task_type)
        if feature is None:
            raise UnsupportedWorkflowTaskTypeError(
                f"Unsupported workflow task type: {task.task_type}"
            )

        model_class = str(task.model_class or "").strip().lower()
        if model_class and model_class not in SUPPORTED_MODEL_CLASSES:
            raise UnsupportedModelClassError("Unsupported model class")

        return AgentProfile(
            name=feature.profile_name,
            skill_names=feature.skill_names,
            requested_tools=feature.requested_tools,
            default_steps=feature.default_steps,
            max_steps=feature.max_steps,
            timeout_seconds=feature.timeout_seconds,
            risk_level=feature.risk_level,
            execution_mode=feature.execution_mode,
            require_plan_approval=feature.require_plan_approval,
            max_review_retries=feature.max_review_retries,
            max_replans=feature.max_replans,
            max_subagents=feature.max_subagents,
            subagent_concurrency=feature.subagent_concurrency,
            subagent_timeout_seconds=feature.subagent_timeout_seconds,
            executor_kind="langgraph",
            workflow_key=f"langgraph.{task_type}",
        )
