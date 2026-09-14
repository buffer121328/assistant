from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import TYPE_CHECKING, AsyncIterator, Mapping, Protocol

from domain.policies.enterprise import GovernedAgentProfile, SubjectContext

if TYPE_CHECKING:
    from agent.ports import AgentRunInput, AgentRunResult


class ExecutionMode(StrEnum):
    """定义当前组件的职责和边界。"""

    INTERACTIVE = "interactive"
    DURABLE = "durable"


@dataclass(frozen=True)
class RuntimeRequest:
    """定义当前接口使用的数据模型。"""

    task_id: str  # task_id 对应的数据字段。
    run_id: str  # run_id 对应的数据字段。
    subject: SubjectContext  # subject 对应的数据字段。
    profile: GovernedAgentProfile  # profile 对应的数据字段。
    run_input: AgentRunInput  # run_input 对应的数据字段。
    mode: ExecutionMode  # mode 对应的数据字段。
    checkpoint_id: str | None = None  # checkpoint_id 对应的数据字段。


@dataclass(frozen=True)
class RuntimeCancelRequest:
    """定义当前接口使用的数据模型。"""

    task_id: str  # task_id 对应的数据字段。
    run_id: str  # run_id 对应的数据字段。
    subject: SubjectContext  # subject 对应的数据字段。


@dataclass(frozen=True)
class AgentEvent:
    """定义当前组件的职责和边界。"""

    type: str  # type 对应的数据字段。
    task_id: str  # task_id 对应的数据字段。
    run_id: str | None  # run_id 对应的数据字段。
    tenant_id: str  # tenant_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    payload: Mapping[str, object]  # payload 对应的数据字段。


class AgentRuntime(Protocol):
    """定义当前组件的接口契约。"""

    async def start(self, request: RuntimeRequest) -> AgentRunResult:
        """Start an interactive or durable run.

        Args:
            request: 当前操作的结构化请求。
        """
        ...

    async def resume(self, request: RuntimeRequest) -> AgentRunResult:
        """Resume the exact durable checkpoint in the request.

        Args:
            request: 当前操作的结构化请求。
        """
        ...

    async def cancel(self, request: RuntimeCancelRequest) -> None:
        """Cancel a run only after the adapter verifies its resource scope.

        Args:
            request: 当前操作的结构化请求。
        """
        ...

    def events(self, *, task_id: str, after: int = 0) -> AsyncIterator[AgentEvent]:
        """Stream normalized events for an already-authorized task.

        Args:
            task_id: 目标任务 ID。
            after: 用于执行当前操作的 after 参数。
        """
        ...


def select_execution_mode(
    *,
    checkpoint_id: str | None = None,
    requires_plan_approval: bool = False,
    allows_subagents: bool = False,
    parallel_steps: int = 0,
) -> ExecutionMode:
    """Select durable mode only for work that requires durable semantics.

    Args:
        checkpoint_id: 用于执行当前操作的 checkpoint id 参数。
        requires_plan_approval: 用于执行当前操作的 requires plan approval 参数。
        allows_subagents: 用于执行当前操作的 allows subagents 参数。
        parallel_steps: 用于执行当前操作的 parallel steps 参数。
    """
    if checkpoint_id or requires_plan_approval or allows_subagents or parallel_steps > 1:
        return ExecutionMode.DURABLE
    return ExecutionMode.INTERACTIVE


_EVENT_ALIASES = {
    "task.started": "run.started",
    "task.completed": "run.completed",
    "task.failed": "run.failed",
    "task.status.changed": "agent.status",
    "task.message.delta": "agent.message.delta",
    "task.tool.requested": "tool.requested",
    "task.waiting_approval": "approval.required",
}
_ALLOWED_PREFIXES = ("run.", "agent.", "tool.", "approval.", "artifact.", "task.")
_SENSITIVE_KEYS = frozenset(
    {"authorization", "cookie", "credential", "password", "secret", "token", "reasoning"}
)
MAX_EVENT_PAYLOAD_BYTES = 16_000


def normalize_agent_event(
    *,
    event_type: str,
    task_id: str,
    tenant_id: str,
    user_id: str,
    payload: Mapping[str, object],
    run_id: str | None = None,
) -> AgentEvent:
    """Normalize, redact, and bound an event before persistence or delivery.

    Args:
        event_type: 用于执行当前操作的 event type 参数。
        task_id: 目标任务 ID。
        tenant_id: 用于执行当前操作的 tenant id 参数。
        user_id: 目标用户 ID。
        payload: 当前操作的结构化载荷。
        run_id: 用于执行当前操作的 run id 参数。
    """
    normalized_type = _EVENT_ALIASES.get(event_type, event_type)
    if not normalized_type.startswith(_ALLOWED_PREFIXES):
        raise ValueError("Unsupported Agent event type")
    safe = _sanitize_mapping(payload)
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
        safe = {"truncated": True, "summary": encoded[:4_000]}
    return AgentEvent(normalized_type, task_id, run_id, tenant_id, user_id, safe)


def _sanitize_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Recursively remove reusable credentials and bound nested event data.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    result: dict[str, object] = {}
    for raw_key, raw_value in list(value.items())[:100]:
        key = str(raw_key)[:128]
        if any(marker in key.lower() for marker in _SENSITIVE_KEYS):
            result[key] = "[REDACTED]"
        elif isinstance(raw_value, Mapping):
            result[key] = _sanitize_mapping(raw_value)
        elif isinstance(raw_value, (list, tuple)):
            result[key] = [
                _sanitize_mapping(item) if isinstance(item, Mapping) else _safe_scalar(item)
                for item in raw_value[:100]
            ]
        else:
            result[key] = _safe_scalar(raw_value)
    return result


def _safe_scalar(value: object) -> object:
    """Keep JSON-compatible scalars and bound arbitrary representations.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4_000]


class LangGraphRuntime:
    """定义当前组件的职责和边界。"""

    def __init__(self, executor: object) -> None:
        """Store an executor implementing the existing async execute contract.

        Args:
            executor: 用于执行当前操作的 executor 参数。
        """
        self._executor = executor

    async def start(self, request: RuntimeRequest) -> AgentRunResult:
        """Execute through the current adapter without exposing graph types.

        Args:
            request: 当前操作的结构化请求。
        """
        execute = getattr(self._executor, "execute")
        return await execute(run_input=request.run_input)

    async def resume(self, request: RuntimeRequest) -> AgentRunResult:
        """Resume only requests that carry an exact durable checkpoint.

        Args:
            request: 当前操作的结构化请求。
        """
        if request.mode is not ExecutionMode.DURABLE or not request.checkpoint_id:
            raise ValueError("Durable resume requires a checkpoint")
        return await self.start(request)

    async def cancel(self, request: RuntimeCancelRequest) -> None:
        """Delegate cancellation when the wrapped runtime supports it.

        Args:
            request: 当前操作的结构化请求。
        """
        cancel = getattr(self._executor, "cancel", None)
        if cancel is None:
            raise RuntimeError("Runtime cancellation is unavailable")
        await cancel(task_id=request.task_id, run_id=request.run_id, user_id=request.subject.user_id)

    async def events(self, *, task_id: str, after: int = 0) -> AsyncIterator[AgentEvent]:
        """Reject direct event access until an authorized event source is injected.

        Args:
            task_id: 目标任务 ID。
            after: 用于执行当前操作的 after 参数。
        """
        if False:
            yield AgentEvent("run.started", task_id, None, "", "", {})
        raise RuntimeError("Runtime event source is unavailable")


class RuntimeExecutorAdapter:
    """定义当前组件的职责和边界。"""

    def __init__(self, runtime: AgentRuntime, *, run_id: str | None) -> None:
        """Bind one worker attempt to the runtime-neutral lifecycle port.

        Args:
            runtime: 用于执行当前操作的 runtime 参数。
            run_id: 用于执行当前操作的 run id 参数。
        """
        self._runtime = runtime
        self._run_id = run_id or "legacy-run"

    async def execute(self, *, run_input: AgentRunInput) -> AgentRunResult:
        """Translate the current harness input into a stable RuntimeRequest.

        Args:
            run_input: 用于执行当前操作的 run input 参数。
        """
        profile = run_input.governed_profile
        subject = (
            profile.subject
            if profile is not None
            else SubjectContext("local", run_input.context.user_id)
        )
        effective_profile = profile or GovernedAgentProfile(subject=subject)
        mode = select_execution_mode(
            requires_plan_approval=run_input.plan.require_plan_approval,
            allows_subagents=run_input.plan.max_subagents > 0,
        )
        return await self._runtime.start(
            RuntimeRequest(
                task_id=run_input.context.task_id,
                run_id=self._run_id,
                subject=subject,
                profile=effective_profile,
                run_input=run_input,
                mode=mode,
            )
        )
