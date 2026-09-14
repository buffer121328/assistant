from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal

from features import feature_for_task_type, planning_task_types
from domain.policies.enterprise import GovernanceValidationError, GovernedAgentProfile
from model_gateway import SUPPORTED_MODEL_CLASSES


ExecutorKind = Literal["langgraph"]
RiskLevel = Literal["low", "medium", "high"]
ExecutionMode = Literal["react", "plan_execute_review"]

SUPPORTED_PLANNING_TASK_TYPES = planning_task_types()
_OFFICE_BASE_SKILL = "office-writing"
_OFFICE_MATCH_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("meeting-minutes", ("会议纪要", "会议记录", "行动项", "minutes")),
    ("business-email", ("邮件", "催办", "邀请函", "email")),
    ("progress-report", ("工作进展", "项目进展", "周报", "复盘", "状态汇报")),
    ("proposal-writing", ("方案", "建议书", "工作计划", "需求说明")),
    ("presentation-briefing", ("汇报提纲", "演示稿", "简报", "ppt", "宣讲")),
    ("structured-spreadsheet", ("表格", "台账", "清单", "excel", "跟踪表")),
    ("technical-daily-report", ("技术日报", "研发日报", "开发日报", "daily report")),
    ("technical-document-layout", ("技术文档排版", "接口文档排版", "设计文档排版")),
    (
        "project-progress-notification",
        (
            "项目进度通知", "进度通知", "上线通知", "发布通知", "里程碑通知",
            "progress notification",
        ),
    ),
    ("problem-observation", ("收集问题", "问题观察", "故障问题", "问题复现", "problem report")),
    ("solution-recommendation", ("解决方案", "解决建议", "临时方案", "solution")),
    ("capability-design", ("能力设计", "设计能力", "skill 设计", "skill开发", "skill 开发")),
    ("mcp-integration-design", ("mcp", "连接器设计", "集成方案", "connector design")),
)
_MAX_MATCHED_OFFICE_SKILLS = 2


class UnsupportedWorkflowTaskTypeError(Exception):
    """表示 处理 unsupported workflow task type error 的后端数据结构或服务对象。"""

    pass


class UnsupportedModelClassError(Exception):
    """表示 处理 unsupported model class error 的后端数据结构或服务对象。"""

    pass


@dataclass(frozen=True)
class AgentProfile:
    """表示 处理 agent profile 的后端数据结构或服务对象。"""

    name: str  # name 对应的数据字段。
    executor_kind: ExecutorKind  # executor_kind 对应的数据字段。
    workflow_key: str  # workflow_key 对应的数据字段。
    skill_names: tuple[str, ...] = ()  # skill_names 对应的数据字段。
    requested_tools: tuple[str, ...] = ()  # requested_tools 对应的数据字段。
    default_steps: tuple[str, ...] = ()  # default_steps 对应的数据字段。
    max_steps: int = 3  # max_steps 对应的数据字段。
    timeout_seconds: float = 60.0  # timeout_seconds 对应的数据字段。
    risk_level: RiskLevel = "low"  # risk_level 对应的数据字段。
    output_format: str = "markdown"  # output_format 对应的数据字段。
    execution_mode: ExecutionMode = "react"  # execution_mode 对应的数据字段。
    require_plan_approval: bool = False  # require_plan_approval 对应的数据字段。
    max_review_retries: int = 0  # max_review_retries 对应的数据字段。
    max_replans: int = 0  # max_replans 对应的数据字段。
    max_subagents: int = 0  # max_subagents 对应的数据字段。
    subagent_concurrency: int = 1  # subagent_concurrency 对应的数据字段。
    subagent_timeout_seconds: float = 30.0  # subagent_timeout_seconds 对应的数据字段。


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

        skill_names = feature.skill_names
        if task_type == "office":
            skill_names = _office_skill_names(task, feature.skill_names)

        return AgentProfile(
            name=feature.profile_name,
            skill_names=skill_names,
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


def _office_skill_names(task: Any, candidates: tuple[str, ...]) -> tuple[str, ...]:
    """Select a bounded authorized subset of office instructions for one request.

    Args:
        task: Current persisted task including its server-created profile snapshot.
        candidates: Server-owned office Skill candidate order.
    """
    allowed = set(candidates)
    snapshot = str(getattr(task, "agent_profile_snapshot", "") or "")
    if snapshot:
        try:
            governed = GovernedAgentProfile.from_snapshot(snapshot)
        except GovernanceValidationError:
            allowed = set()
        else:
            allowed.intersection_update(governed.skills)
            if not governed.skills and any(
                item.split("@", maxsplit=1)[0] == "profile.office"
                for item in governed.capabilities
            ):
                allowed.add(_OFFICE_BASE_SKILL)

    selected: list[str] = []
    try:
        explicit = tuple(json.loads(getattr(task, "requested_skill_names_json", "[]") or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        explicit = ()
    selected.extend(item for item in explicit if item in allowed)
    if _OFFICE_BASE_SKILL in allowed and _OFFICE_BASE_SKILL not in selected:
        selected.append(_OFFICE_BASE_SKILL)
    normalized_input = str(getattr(task, "input_text", "") or "").lower()
    for skill_name, keywords in _OFFICE_MATCH_RULES:
        if len(selected) >= 1 + _MAX_MATCHED_OFFICE_SKILLS:
            break
        if skill_name in allowed and skill_name not in selected and any(keyword in normalized_input for keyword in keywords):
            selected.append(skill_name)
    return tuple(selected)
