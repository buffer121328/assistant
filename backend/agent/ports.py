from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from agent.planning.context import TaskContext
from agent.planning.planner import ExecutionPlan
from domain.policies.enterprise import GovernedAgentProfile


TaskRecordT = TypeVar("TaskRecordT", covariant=True)
UserRecordT = TypeVar("UserRecordT", covariant=True)


@dataclass(frozen=True)
class AgentRunInput:
    """表示 处理 agent run input 的后端数据结构或服务对象。"""

    plan: ExecutionPlan  # plan 对应的数据字段。
    context: TaskContext  # context 对应的数据字段。
    governed_profile: GovernedAgentProfile | None = None  # governed_profile 对应的数据字段。


ApprovalTypeName = Literal["tool", "plan", "review", "change"]


@dataclass(frozen=True)
class HumanApprovalRequest:
    """表示 处理 human approval request 的后端数据结构或服务对象。"""

    approval_type: ApprovalTypeName  # approval_type 对应的数据字段。
    subject: str  # subject 对应的数据字段。
    summary: str  # summary 对应的数据字段。
    tool_name: str | None = None  # tool_name 对应的数据字段。
    request_fingerprint: str | None = None  # request_fingerprint 对应的数据字段。


@dataclass(frozen=True)
class AgentRunResult:
    """表示 处理 agent run result 的后端数据结构或服务对象。"""

    result_text: str  # result_text 对应的数据字段。
    display_plan: tuple[str, ...] = ()  # display_plan 对应的数据字段。
    tool_calls: tuple[str, ...] = ()  # tool_calls 对应的数据字段。
    requested_tools: tuple[str, ...] = ()  # requested_tools 对应的数据字段。
    loop_steps: int = 1  # loop_steps 对应的数据字段。
    checkpoint_id: str | None = None  # checkpoint_id 对应的数据字段。
    approval_requests: tuple[HumanApprovalRequest, ...] = ()  # approval_requests 对应的数据字段。


class AgentExecutorProtocol(Protocol):
    """表示 处理 agent executor protocol 的后端数据结构或服务对象。"""

    async def execute(self, *, run_input: AgentRunInput) -> AgentRunResult:
        """执行。

        Args:
            run_input: run_input 参数。
        """
        ...


@dataclass(frozen=True)
class HarnessTaskRecord:
    """表示 处理 harness task record 的后端数据结构或服务对象。"""

    id: str  # id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    task_type: str  # task_type 对应的数据字段。
    input_text: str  # input_text 对应的数据字段。
    status: str  # status 对应的数据字段。
    platform: str  # platform 对应的数据字段。
    workflow_key: str | None = None  # workflow_key 对应的数据字段。
    model_class: str | None = None  # model_class 对应的数据字段。
    conversation_id: str | None = None  # conversation_id 对应的数据字段。


class TaskLifecyclePort(Protocol[TaskRecordT]):
    """表示 处理 task lifecycle port 的后端数据结构或服务对象。"""

    async def load_pending(self, task_id: str) -> TaskRecordT:
        """Load a pending task or raise a domain-specific error.

        Args:
            task_id: 目标任务 ID。
        """

    async def mark_running(
        self, task_id: str, *, workflow_key: str | None = None
    ) -> TaskRecordT:
        """Move a task into the running phase.

        Args:
            task_id: 目标任务 ID。
            workflow_key: 用于执行当前操作的 workflow key 参数。
        """

    async def save_success(self, task_id: str, result_text: str) -> TaskRecordT:
        """Persist a successful task result.

        Args:
            task_id: 目标任务 ID。
            result_text: 用于执行当前操作的 result text 参数。
        """

    async def save_failure(self, task_id: str, error_message: str) -> TaskRecordT:
        """Persist a failed task result.

        Args:
            task_id: 目标任务 ID。
            error_message: 用于执行当前操作的 error message 参数。
        """

    async def save_waiting_approval(
        self,
        task_id: str,
        message: str,
        *,
        requested_tools: Iterable[str] = (),
        approval_requests: Iterable[object] = (),
    ) -> TaskRecordT:
        """Persist an approval interrupt and expose it to the task owner.

        Args:
            task_id: 目标任务 ID。
            message: 用于执行当前操作的 message 参数。
            requested_tools: 用于执行当前操作的 requested tools 参数。
            approval_requests: 用于执行当前操作的 approval requests 参数。
        """


class UserLookupPort(Protocol[UserRecordT]):
    """表示 处理 user lookup port 的后端数据结构或服务对象。"""

    async def load_user(self, user_id: str) -> UserRecordT:
        """Load an agent-visible user record.

        Args:
            user_id: 目标用户 ID。
        """


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
        """Persist an executor trace record.

        Args:
            task_id: 目标任务 ID。
            tool_name: 用于执行当前操作的 tool name 参数。
            status: 目标状态。
            input_text: 用于执行当前操作的 input text 参数。
            output_text: 用于执行当前操作的 output text 参数。
            error_message: 用于执行当前操作的 error message 参数。
        """


class LocalTaskServicePort(Protocol[TaskRecordT]):
    """表示 处理 local task service port 的后端数据结构或服务对象。"""

    async def execute_memory_task(self, task_id: str) -> TaskRecordT:
        """Execute a deterministic memory command task.

        Args:
            task_id: 目标任务 ID。
        """

    async def execute_status_task(self, task_id: str) -> TaskRecordT:
        """Execute a deterministic local status command task.

        Args:
            task_id: 目标任务 ID。
        """


@dataclass(frozen=True)
class ConversationContextPack:
    """表示 处理 conversation context pack 的后端数据结构或服务对象。"""

    history: tuple[tuple[str, str], ...] = ()  # history 对应的数据字段。
    summary: str = ""  # summary 对应的数据字段。
    memory_blocks: tuple[str, ...] = ()  # memory_blocks 对应的数据字段。
    trace: tuple[dict[str, object], ...] = ()  # trace 对应的数据字段。
    compacted: bool = False  # compacted 对应的数据字段。


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
        """Return compacted conversation context for an agent run.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
            task_id: 目标任务 ID。
            current_input: 用于执行当前操作的 current input 参数。
            long_term_memory: 用于执行当前操作的 long term memory 参数。
        """


class MemoryContextPort(Protocol):
    """表示 处理 memory context port 的后端数据结构或服务对象。"""

    async def load_context(self, *, user_id: str, query: str, limit: int) -> str:
        """Return memory context text for an agent run.

        Args:
            user_id: 目标用户 ID。
            query: 用于执行当前操作的 query 参数。
            limit: 返回结果的最大数量。
        """


class StatusContextPort(Protocol):
    """表示 处理 status context port 的后端数据结构或服务对象。"""

    async def build_status(self, *, user_id: str) -> str:
        """Return user-visible local status context for planning.

        Args:
            user_id: 目标用户 ID。
        """
