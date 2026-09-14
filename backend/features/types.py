from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExecutionMode = Literal["react", "plan_execute_review"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class FeatureDefinition:
    """表示 处理 feature definition 的后端数据结构或服务对象。"""

    command: str  # command 对应的数据字段。
    task_type: str  # task_type 对应的数据字段。
    profile_name: str  # profile_name 对应的数据字段。
    skill_names: tuple[str, ...]  # skill_names 对应的数据字段。
    requested_tools: tuple[str, ...]  # requested_tools 对应的数据字段。
    default_steps: tuple[str, ...]  # default_steps 对应的数据字段。
    max_steps: int = 3  # max_steps 对应的数据字段。
    timeout_seconds: float = 60.0  # timeout_seconds 对应的数据字段。
    risk_level: RiskLevel = "low"  # risk_level 对应的数据字段。
    execution_mode: ExecutionMode = "react"  # execution_mode 对应的数据字段。
    require_plan_approval: bool = False  # require_plan_approval 对应的数据字段。
    max_review_retries: int = 0  # max_review_retries 对应的数据字段。
    max_replans: int = 0  # max_replans 对应的数据字段。
    max_subagents: int = 0  # max_subagents 对应的数据字段。
    subagent_concurrency: int = 1  # subagent_concurrency 对应的数据字段。
    subagent_timeout_seconds: float = 30.0  # subagent_timeout_seconds 对应的数据字段。
