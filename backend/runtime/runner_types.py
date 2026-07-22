from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.modeling.executors import AgentRunResult


LANGGRAPH_EXECUTOR_TOOL_NAME = "langgraph.executor"
TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_WAITING_APPROVAL = "waiting_approval"


class AgentHarnessError(Exception):
    """Base error raised by the runtime agent harness."""


class NonPendingTaskExecutionError(AgentHarnessError):
    """Raised when a non-pending task is submitted to the harness."""


LangGraphExecutionResult = AgentRunResult


@dataclass(frozen=True)
class ExecutionOutcome:
    """Normalized outcome returned by an execution boundary."""

    status: str
    result_text: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    workflow_key: str | None = None


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
