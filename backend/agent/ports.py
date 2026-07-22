from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, TypeVar


TaskRecordT = TypeVar("TaskRecordT", covariant=True)
UserRecordT = TypeVar("UserRecordT", covariant=True)


@dataclass(frozen=True)
class HarnessTaskRecord:
    """表示 处理 harness task record 的后端数据结构或服务对象。"""

    id: str
    user_id: str
    task_type: str
    input_text: str
    status: str
    platform: str
    workflow_key: str | None = None
    model_class: str | None = None
    conversation_id: str | None = None


class TaskLifecyclePort(Protocol[TaskRecordT]):
    """表示 处理 task lifecycle port 的后端数据结构或服务对象。"""

    async def load_pending(self, task_id: str) -> TaskRecordT:
        """Load a pending task or raise a domain-specific error."""

    async def mark_running(
        self, task_id: str, *, workflow_key: str | None = None
    ) -> TaskRecordT:
        """Move a task into the running phase."""

    async def save_success(self, task_id: str, result_text: str) -> TaskRecordT:
        """Persist a successful task result."""

    async def save_failure(self, task_id: str, error_message: str) -> TaskRecordT:
        """Persist a failed task result."""

    async def save_waiting_approval(
        self,
        task_id: str,
        message: str,
        *,
        requested_tools: Iterable[str] = (),
        approval_requests: Iterable[object] = (),
    ) -> TaskRecordT:
        """Persist an approval interrupt and expose it to the task owner."""


class UserLookupPort(Protocol[UserRecordT]):
    """表示 处理 user lookup port 的后端数据结构或服务对象。"""

    async def load_user(self, user_id: str) -> UserRecordT:
        """Load an agent-visible user record."""


class ExecutionTracePort(Protocol):
    """表示 处理 execution trace port 的后端数据结构或服务对象。"""

    async def record_trace(
        self,
        *,
        task_id: str,
        tool_name: str,
        status: str,
        input_text: str,
        output_text: str | None,
        error_message: str | None,
    ) -> None:
        """Persist an executor trace record."""


class LocalTaskServicePort(Protocol[TaskRecordT]):
    """表示 处理 local task service port 的后端数据结构或服务对象。"""

    async def execute_memory_task(self, task_id: str) -> TaskRecordT:
        """Execute a deterministic memory command task."""

    async def execute_status_task(self, task_id: str) -> TaskRecordT:
        """Execute a deterministic local status command task."""


@dataclass(frozen=True)
class ConversationContextPack:
    """表示 处理 conversation context pack 的后端数据结构或服务对象。"""

    history: tuple[tuple[str, str], ...] = ()
    summary: str = ""
    memory_blocks: tuple[str, ...] = ()
    trace: tuple[dict[str, object], ...] = ()
    compacted: bool = False


class ConversationContextPort(Protocol):
    """表示 处理 conversation context port 的后端数据结构或服务对象。"""

    async def load_context(
        self,
        *,
        conversation_id: str,
        user_id: str,
        task_id: str,
        current_input: str,
        long_term_memory: str,
    ) -> ConversationContextPack:
        """Return compacted conversation context for an agent run."""


class MemoryContextPort(Protocol):
    """表示 处理 memory context port 的后端数据结构或服务对象。"""

    async def load_context(self, *, user_id: str, query: str, limit: int) -> str:
        """Return memory context text for an agent run."""


class StatusContextPort(Protocol):
    """表示 处理 status context port 的后端数据结构或服务对象。"""

    async def build_status(self, *, user_id: str) -> str:
        """Return user-visible local status context for planning."""
