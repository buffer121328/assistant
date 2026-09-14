from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.ports import AgentRunResult


LANGGRAPH_EXECUTOR_TOOL_NAME = "langgraph.executor"
TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_WAITING_APPROVAL = "waiting_approval"


class AgentHarnessError(Exception):
    """定义当前组件可安全处理的错误类型。"""


class NonPendingTaskExecutionError(AgentHarnessError):
    """定义当前组件可安全处理的错误类型。"""


LangGraphExecutionResult = AgentRunResult


@dataclass(frozen=True)
class ExecutionOutcome:
    """定义当前组件的职责和边界。"""

    status: str  # status 对应的数据字段。
    result_text: str | None = None  # result_text 对应的数据字段。
    error_message: str | None = None  # error_message 对应的数据字段。
    metadata: dict[str, Any] = field(default_factory=dict)  # metadata 对应的数据字段。
    workflow_key: str | None = None  # workflow_key 对应的数据字段。


__all__ = [
    "AgentHarnessError",
    "ExecutionOutcome",
    "LANGGRAPH_EXECUTOR_TOOL_NAME",
    "LangGraphExecutionResult",
    "NonPendingTaskExecutionError",
    "TASK_STATUS_FAILED",
    "TASK_STATUS_PENDING",
    "TASK_STATUS_RUNNING",
    "TASK_STATUS_SUCCESS",
    "TASK_STATUS_WAITING_APPROVAL",
]
