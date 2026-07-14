from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from packages.model_gateway import SUPPORTED_MODEL_CLASSES


ExecutorKind = Literal["langgraph"]
RiskLevel = Literal["low", "medium", "high"]
ExecutionMode = Literal["react", "plan_execute_review"]

SUPPORTED_PLANNING_TASK_TYPES = frozenset({"plan", "learn", "daily", "office"})


class UnsupportedWorkflowTaskTypeError(Exception):
    pass


class UnsupportedModelClassError(Exception):
    pass


@dataclass(frozen=True)
class AgentProfile:
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


_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "plan": {
        "name": "v2.planner",
        "skill_names": ("structured-planning",),
        "requested_tools": ("calendar.create_event",),
        "default_steps": (
            "明确目标与约束",
            "拆解阶段步骤",
            "给出下一步行动",
        ),
        "max_steps": 3,
        "timeout_seconds": 60.0,
        "risk_level": "low",
    },
    "learn": {
        "name": "v2.researcher",
        "skill_names": ("research",),
        "requested_tools": ("search.web", "browser.read"),
        "default_steps": (
            "读取任务上下文与记忆",
            "检索并核对来源",
            "提炼学习结论",
        ),
        "max_steps": 12,
        "timeout_seconds": 90.0,
        "risk_level": "medium",
        "execution_mode": "plan_execute_review",
        "max_review_retries": 1,
        "max_replans": 1,
        "max_subagents": 2,
        "subagent_concurrency": 2,
    },
    "daily": {
        "name": "v2.daily_reporter",
        "skill_names": ("research", "daily-report"),
        "requested_tools": ("search.web", "browser.read", "email.draft"),
        "default_steps": (
            "读取任务上下文与记忆",
            "检索并整理当日线索",
            "输出日报摘要",
        ),
        "max_steps": 12,
        "timeout_seconds": 90.0,
        "risk_level": "medium",
        "execution_mode": "plan_execute_review",
        "max_review_retries": 1,
        "max_replans": 1,
        "max_subagents": 2,
        "subagent_concurrency": 2,
    },
    "office": {
        "name": "v2.office_writer",
        "skill_names": ("office-writing",),
        "requested_tools": (
            "email.draft",
            "calendar.create_event",
            "office.create_docx",
            "office.create_xlsx",
            "office.create_pptx",
        ),
        "default_steps": (
            "读取任务上下文与记忆",
            "整理输入材料",
            "输出结构化文本",
        ),
        "max_steps": 12,
        "timeout_seconds": 90.0,
        "risk_level": "low",
        "execution_mode": "plan_execute_review",
        "max_review_retries": 1,
        "max_replans": 1,
        "max_subagents": 2,
        "subagent_concurrency": 2,
    },
}

class DefaultProfileSelector:
    def select(self, task: Any) -> AgentProfile:
        task_type = str(task.task_type).strip()
        try:
            defaults = _PROFILE_DEFAULTS[task_type]
        except KeyError as exc:
            raise UnsupportedWorkflowTaskTypeError(
                f"Unsupported workflow task type: {task.task_type}"
            ) from exc

        model_class = str(task.model_class or "").strip().lower()
        if model_class and model_class not in SUPPORTED_MODEL_CLASSES:
            raise UnsupportedModelClassError("Unsupported model class")

        return AgentProfile(
            **defaults,
            executor_kind="langgraph",
            workflow_key=f"langgraph.{task_type}",
        )
