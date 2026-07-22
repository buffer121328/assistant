from __future__ import annotations

from typing import Any, TypedDict


_AGENT_CORE_VERSION = "v2"


class _ExecutionState(TypedDict, total=False):
    """表示 处理 execution state 的后端数据结构或服务对象。"""

    tool_schemas: list[dict[str, Any]]
    history: list[dict[str, Any]]
    decision: dict[str, Any]
    display_plan: list[str]
    sources: list[dict[str, Any]]
    tool_calls: list[str]
    requested_tools: list[str]
    result_text: str
    candidate_result: str
    work_plan: dict[str, Any]
    review_decision: dict[str, str]
    review_feedback: str
    review_retry_count: int
    replan_count: int
    step_count: int
    subagent_results: list[dict[str, Any]]
