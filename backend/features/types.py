from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExecutionMode = Literal["react", "plan_execute_review"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class FeatureDefinition:
    """表示 处理 feature definition 的后端数据结构或服务对象。"""

    command: str
    task_type: str
    profile_name: str
    skill_names: tuple[str, ...]
    requested_tools: tuple[str, ...]
    default_steps: tuple[str, ...]
    max_steps: int = 3
    timeout_seconds: float = 60.0
    risk_level: RiskLevel = "low"
    execution_mode: ExecutionMode = "react"
    require_plan_approval: bool = False
    max_review_retries: int = 0
    max_replans: int = 0
    max_subagents: int = 0
    subagent_concurrency: int = 1
    subagent_timeout_seconds: float = 30.0
