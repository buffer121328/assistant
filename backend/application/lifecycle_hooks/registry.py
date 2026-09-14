from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
import time
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.models import LifecycleHookExecution, utc_now
from domain.policies.redaction import sanitize_text


HookMode = Literal["observer", "guard"]
HookDecision = Literal["allow", "deny", "require_approval"]
HookResult = Literal[
    "succeeded",
    "failed",
    "timeout",
    "allow",
    "deny",
    "require_approval",
    "skipped",
]

SUPPORTED_EVENT_TYPES = frozenset(
    {
        "task.created",
        "task.status.changed",
        "task.message.delta",
        "task.message.completed",
        "task.plan.created",
        "task.action.started",
        "task.action.completed",
        "task.action.failed",
        "plan",
        "governance.decision",
        "governance.content",
        "task.started",
        "task.completed",
        "task.failed",
        "task.waiting_approval",
        "run.started",
        "run.completed",
        "run.failed",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "approval.requested",
        "approval.resolved",
        "artifact.registered",
        "artifact.archived",
        "artifact.revoked",
        "before.tool",
        "before.external.write",
        "before.email.send",
        "before.calendar.write",
        "before.document.write",
    }
)
_ALLOWED_SOURCE_KINDS = frozenset(
    {"builtin", "managed_organization", "trusted_connector"}
)
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_MAX_PAYLOAD_BYTES = 16_000
_MAX_TEXT_BYTES = 2_000
_MAX_TIMEOUT_SECONDS = 30.0


class LifecycleHookError(ValueError):
    """定义当前组件可安全处理的错误类型。"""


class HookRegistrationError(LifecycleHookError):
    """定义当前组件可安全处理的错误类型。"""


class LifecycleEventError(LifecycleHookError):
    """定义当前组件可安全处理的错误类型。"""


@dataclass(frozen=True)
class LifecycleEvent:
    """定义当前组件的职责和边界。"""

    event_id: str  # event_id 对应的数据字段。
    event_type: str  # event_type 对应的数据字段。
    task_id: str | None  # task_id 对应的数据字段。
    run_id: str | None  # run_id 对应的数据字段。
    actor_user_id: str  # actor_user_id 对应的数据字段。
    tenant_id: str  # tenant_id 对应的数据字段。
    organization_id: str | None  # organization_id 对应的数据字段。
    conversation_id: str | None  # conversation_id 对应的数据字段。
    occurred_at: datetime  # occurred_at 对应的数据字段。
    idempotency_key: str  # idempotency_key 对应的数据字段。
    payload: Mapping[str, object]  # payload 对应的数据字段。
    resource_ids: tuple[str, ...] = ()  # resource_ids 对应的数据字段。


@dataclass(frozen=True)
class HookMatcher:
    """定义当前组件的职责和边界。"""

    event_types: frozenset[str]  # event_types 对应的数据字段。
    task_ids: frozenset[str] = frozenset()  # task_ids 对应的数据字段。
    resource_ids: frozenset[str] = frozenset()  # resource_ids 对应的数据字段。

    def matches(self, event: LifecycleEvent) -> bool:
        """判断输入是否满足当前匹配条件。

        Args:
            event: 需要处理的生命周期事件。
        """
        if event.event_type not in self.event_types:
            return False
        if self.task_ids and event.task_id not in self.task_ids:
            return False
        if self.resource_ids and not self.resource_ids.intersection(event.resource_ids):
            return False
        return True


HookHandler = Callable[[LifecycleEvent], Awaitable[HookDecision | None]]


@dataclass(frozen=True)
class HookDefinition:
    """定义当前组件的职责和边界。"""

    name: str  # name 对应的数据字段。
    version: str  # version 对应的数据字段。
    source_kind: str  # source_kind 对应的数据字段。
    source_id: str  # source_id 对应的数据字段。
    matcher: HookMatcher  # matcher 对应的数据字段。
    mode: HookMode  # mode 对应的数据字段。
    handler: HookHandler  # handler 对应的数据字段。
    priority: int = 0  # priority 对应的数据字段。
    timeout_seconds: float = 5.0  # timeout_seconds 对应的数据字段。
    fail_closed: bool = True  # fail_closed 对应的数据字段。
    import_path: str | None = None  # import_path 对应的数据字段。
    shell_command: str | None = None  # shell_command 对应的数据字段。


@dataclass(frozen=True)
class HookExecutionResult:
    """定义当前组件的职责和边界。"""

    hook_name: str  # hook_name 对应的数据字段。
    hook_version: str  # hook_version 对应的数据字段。
    mode: HookMode  # mode 对应的数据字段。
    result: HookResult  # result 对应的数据字段。
    reason: str = ""  # reason 对应的数据字段。
    failure_class: str | None = None  # failure_class 对应的数据字段。
    duration_ms: int = 0  # duration_ms 对应的数据字段。
    skipped: bool = False  # skipped 对应的数据字段。


@dataclass(frozen=True)
class GuardDispatchResult:
    """定义当前组件的职责和边界。"""

    decision: HookDecision  # decision 对应的数据字段。
    reasons: tuple[str, ...] = ()  # reasons 对应的数据字段。
    executions: tuple[HookExecutionResult, ...] = ()  # executions 对应的数据字段。


@dataclass
class _MemoryExecution:
    """_MemoryExecution 的职责定义。"""
    result: HookExecutionResult  # result 对应的数据字段。


def build_lifecycle_event(
    *,
    event_type: str,
    actor_user_id: str,
    tenant_id: str,
    task_id: str | None = None,
    run_id: str | None = None,
    organization_id: str | None = None,
    conversation_id: str | None = None,
    payload: Mapping[str, object] | None = None,
    resource_ids: Sequence[str] = (),
    idempotency_key: str | None = None,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
) -> LifecycleEvent:
    """Build one bounded event and derive stable identity when omitted.

    Args:
        event_type: 用于执行当前操作的 event type 参数。
        actor_user_id: 发起当前操作的用户 ID。
        tenant_id: 用于执行当前操作的 tenant id 参数。
        task_id: 目标任务 ID。
        run_id: 用于执行当前操作的 run id 参数。
        organization_id: 用于执行当前操作的 organization id 参数。
        conversation_id: 目标会话 ID。
        payload: 当前操作的结构化载荷。
        resource_ids: 用于执行当前操作的 resource ids 参数。
        idempotency_key: 用于识别重复请求的幂等键。
        event_id: 用于执行当前操作的 event id 参数。
        occurred_at: 用于执行当前操作的 occurred at 参数。
    """
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise LifecycleEventError(f"Unsupported lifecycle event: {event_type}")
    if not actor_user_id.strip() or not tenant_id.strip():
        raise LifecycleEventError("Lifecycle event requires actor and tenant")

    safe_payload = _sanitize_payload(payload or {})
    normalized_resources = tuple(
        sorted({item.strip() for item in resource_ids if item.strip()})
    )
    occurred = occurred_at or utc_now()
    canonical = json.dumps(
        {
            "event_type": event_type,
            "actor_user_id": actor_user_id,
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "run_id": run_id,
            "payload": safe_payload,
            "resource_ids": normalized_resources,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    stable_key = idempotency_key or sha256(canonical.encode("utf-8")).hexdigest()
    stable_event_id = (
        event_id or sha256(f"event:{stable_key}".encode("utf-8")).hexdigest()
    )
    return LifecycleEvent(
        event_id=stable_event_id,
        event_type=event_type,
        task_id=task_id,
        run_id=run_id,
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        organization_id=organization_id,
        conversation_id=conversation_id,
        occurred_at=occurred,
        idempotency_key=stable_key,
        payload=safe_payload,
        resource_ids=normalized_resources,
    )


class LifecycleHookRegistry:
    """定义当前组件的职责和边界。"""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """初始化对象所需的运行时依赖和受限状态。

        Args:
            sessionmaker: 用于创建独立数据库会话的工厂。
            session: 当前数据库异步会话。
        """
        self.sessionmaker = sessionmaker
        self.session = session
        self._hooks: dict[str, HookDefinition] = {}
        self._memory_executions: dict[str, _MemoryExecution] = {}

    @property
    def hooks(self) -> tuple[HookDefinition, ...]:
        """执行当前组件定义的业务处理逻辑。"""
        return tuple(
            sorted(self._hooks.values(), key=lambda item: (-item.priority, item.name))
        )

    def register(self, hook: HookDefinition) -> None:
        """Validate and register a trusted server-composed Hook.

        Args:
            hook: 待处理的生命周期钩子定义。
        """
        _validate_hook(hook)
        if hook.name in self._hooks:
            raise HookRegistrationError(f"Duplicate lifecycle Hook: {hook.name}")
        self._hooks[hook.name] = hook

    async def dispatch_observers(
        self,
        event: LifecycleEvent,
    ) -> tuple[HookExecutionResult, ...]:
        """Run matching observers best-effort without changing core state.

        Args:
            event: 需要处理的生命周期事件。
        """
        executions: list[HookExecutionResult] = []
        for hook in self._matching(event, mode="observer"):
            result = await self._dispatch_one(event, hook)
            executions.append(result)
        return tuple(executions)

    async def dispatch_guards(self, event: LifecycleEvent) -> GuardDispatchResult:
        """Run matching guards and aggregate deny > approval > allow.

        Args:
            event: 需要处理的生命周期事件。
        """
        executions: list[HookExecutionResult] = []
        decisions: list[HookDecision] = []
        reasons: list[str] = []
        for hook in self._matching(event, mode="guard"):
            execution = await self._dispatch_one(event, hook)
            executions.append(execution)
            if execution.result in {"allow", "deny", "require_approval"}:
                decisions.append(cast(HookDecision, execution.result))
            elif execution.result in {"failed", "timeout"}:
                fallback: HookDecision = (
                    "deny" if hook.fail_closed else "require_approval"
                )
                decisions.append(fallback)
            if execution.reason:
                reasons.append(execution.reason)
        decision = (
            max(decisions, key=lambda value: _DECISION_RANK[value])
            if decisions
            else "allow"
        )
        return GuardDispatchResult(
            decision=decision,
            reasons=tuple(reasons),
            executions=tuple(executions),
        )

    def _matching(
        self,
        event: LifecycleEvent,
        *,
        mode: HookMode,
    ) -> tuple[HookDefinition, ...]:
        """执行 matching 的内部处理逻辑。

        Args:
            event: 需要处理的生命周期事件。
            mode: 用于执行当前操作的 mode 参数。
        """
        return tuple(
            hook
            for hook in self.hooks
            if hook.mode == mode and hook.matcher.matches(event)
        )

    async def _dispatch_one(
        self,
        event: LifecycleEvent,
        hook: HookDefinition,
    ) -> HookExecutionResult:
        """执行 dispatch one 的内部处理逻辑。

        Args:
            event: 需要处理的生命周期事件。
            hook: 待处理的生命周期钩子定义。
        """
        execution_key = _execution_key(event, hook)
        existing = await self._find_execution(execution_key)
        if existing is not None and existing.result == "succeeded":
            return HookExecutionResult(
                hook_name=hook.name,
                hook_version=hook.version,
                mode=hook.mode,
                result="skipped",
                reason="already_succeeded",
                skipped=True,
            )

        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                hook.handler(event), timeout=hook.timeout_seconds
            )
            result: HookResult
            reason = ""
            if hook.mode == "guard":
                result = cast(HookResult, response or "allow")
                if result not in {"allow", "deny", "require_approval"}:
                    raise LifecycleHookError("Guard returned an invalid decision")
                reason = _safe_reason(f"guard_{result}")
            else:
                result = "succeeded"
                if response is not None:
                    reason = _safe_reason(str(response))
            failure_class = None
        except asyncio.TimeoutError:
            result = "timeout"
            reason = "Hook timed out"
            failure_class = "timeout"
        except Exception as exc:  # Hooks are isolated from core lifecycle facts.
            result = "failed"
            reason = "Hook execution failed"
            failure_class = type(exc).__name__[:64]
        duration_ms = int((time.perf_counter() - started) * 1000)
        summary = HookExecutionResult(
            hook_name=hook.name,
            hook_version=hook.version,
            mode=hook.mode,
            result=result,
            reason=reason,
            failure_class=failure_class,
            duration_ms=duration_ms,
        )
        await self._record_execution(event, hook, summary, execution_key=execution_key)
        return summary

    async def _find_execution(self, execution_key: str) -> HookExecutionResult | None:
        """执行 find execution 的内部处理逻辑。

        Args:
            execution_key: 用于执行当前操作的 execution key 参数。
        """
        if self.session is not None:
            return await self._execution_result(self.session, execution_key)
        if self.sessionmaker is None:
            memory = self._memory_executions.get(execution_key)
            return memory.result if memory is not None else None
        async with self.sessionmaker() as session:
            return await self._execution_result(session, execution_key)

    async def _record_execution(
        self,
        event: LifecycleEvent,
        hook: HookDefinition,
        summary: HookExecutionResult,
        *,
        execution_key: str,
    ) -> None:
        """执行 record execution 的内部处理逻辑。

        Args:
            event: 需要处理的生命周期事件。
            hook: 待处理的生命周期钩子定义。
            summary: 用于执行当前操作的 summary 参数。
            execution_key: 用于执行当前操作的 execution key 参数。
        """
        if self.session is not None:
            await self._record_in_active_transaction(
                self.session,
                event,
                hook,
                summary,
                execution_key=execution_key,
            )
            return
        if self.sessionmaker is None:
            self._memory_executions[execution_key] = _MemoryExecution(summary)
            return
        async with self.sessionmaker() as session:
            await self._record_in_owned_transaction(
                session,
                event,
                hook,
                summary,
                execution_key=execution_key,
            )

    @staticmethod
    async def _execution_result(
        session: AsyncSession,
        execution_key: str,
    ) -> HookExecutionResult | None:
        """执行 execution result 的内部处理逻辑。

        Args:
            session: 当前数据库异步会话。
            execution_key: 用于执行当前操作的 execution key 参数。
        """
        row = await session.scalar(
            select(LifecycleHookExecution).where(
                LifecycleHookExecution.execution_key == execution_key
            )
        )
        if row is None:
            return None
        return HookExecutionResult(
            hook_name=row.hook_name,
            hook_version=row.hook_version,
            mode=cast(HookMode, row.mode),
            result=cast(HookResult, row.result),
            reason=row.reason,
            failure_class=row.failure_class,
            duration_ms=row.duration_ms,
            skipped=row.result == "skipped",
        )

    async def _record_in_active_transaction(
        self,
        session: AsyncSession,
        event: LifecycleEvent,
        hook: HookDefinition,
        summary: HookExecutionResult,
        *,
        execution_key: str,
    ) -> None:
        """Persist through the caller's unit of work without a competing writer.

        Args:
            session: 当前数据库异步会话。
            event: 需要处理的生命周期事件。
            hook: 待处理的生命周期钩子定义。
            summary: 用于执行当前操作的 summary 参数。
            execution_key: 用于执行当前操作的 execution key 参数。
        """
        async with session.begin_nested():
            await self._upsert_execution(
                session,
                event,
                hook,
                summary,
                execution_key=execution_key,
            )
            await session.flush()

    async def _record_in_owned_transaction(
        self,
        session: AsyncSession,
        event: LifecycleEvent,
        hook: HookDefinition,
        summary: HookExecutionResult,
        *,
        execution_key: str,
    ) -> None:
        """执行 record in owned transaction 的内部处理逻辑。

        Args:
            session: 当前数据库异步会话。
            event: 需要处理的生命周期事件。
            hook: 待处理的生命周期钩子定义。
            summary: 用于执行当前操作的 summary 参数。
            execution_key: 用于执行当前操作的 execution key 参数。
        """
        try:
            await self._upsert_execution(
                session,
                event,
                hook,
                summary,
                execution_key=execution_key,
            )
            await session.commit()
        except IntegrityError:
            # A concurrent dispatcher may have persisted the same stable
            # event/Hook key after our initial lookup. Preserve the existing
            # record rather than surfacing a uniqueness error to the producer.
            await session.rollback()
            concurrent = await self._execution_result(session, execution_key)
            if concurrent is None:
                raise

    @staticmethod
    async def _upsert_execution(
        session: AsyncSession,
        event: LifecycleEvent,
        hook: HookDefinition,
        summary: HookExecutionResult,
        *,
        execution_key: str,
    ) -> None:
        """执行 upsert execution 的内部处理逻辑。

        Args:
            session: 当前数据库异步会话。
            event: 需要处理的生命周期事件。
            hook: 待处理的生命周期钩子定义。
            summary: 用于执行当前操作的 summary 参数。
            execution_key: 用于执行当前操作的 execution key 参数。
        """
        row = await session.scalar(
            select(LifecycleHookExecution).where(
                LifecycleHookExecution.execution_key == execution_key
            )
        )
        is_retry = row is not None
        if row is None:
            row = LifecycleHookExecution(
                execution_key=execution_key,
                event_id=event.event_id,
                event_type=event.event_type,
                tenant_id=event.tenant_id,
                subject_id=event.actor_user_id,
                conversation_id=event.conversation_id,
                task_id=event.task_id,
                run_id=event.run_id,
                hook_name=hook.name,
                hook_version=hook.version,
                hook_source=f"{hook.source_kind}:{hook.source_id}",
                mode=hook.mode,
            )
            session.add(row)
        row.result = summary.result
        row.failure_class = summary.failure_class
        row.reason = _safe_reason(summary.reason)
        row.duration_ms = summary.duration_ms
        row.retry_state = "retry" if is_retry else "first_attempt"
        row.finished_at = utc_now()


def _validate_hook(hook: HookDefinition) -> None:
    """执行 validate hook 的内部处理逻辑。

    Args:
        hook: 待处理的生命周期钩子定义。
    """
    if not _NAME_PATTERN.fullmatch(hook.name):
        raise HookRegistrationError("Invalid Hook name")
    if not hook.version.strip() or not hook.source_id.strip():
        raise HookRegistrationError("Hook version and source are required")
    if hook.source_kind not in _ALLOWED_SOURCE_KINDS:
        raise HookRegistrationError("Hook source is not trusted")
    if hook.mode not in {"observer", "guard"}:
        raise HookRegistrationError("Unsupported Hook mode")
    if not hook.matcher.event_types or not hook.matcher.event_types.issubset(
        SUPPORTED_EVENT_TYPES
    ):
        raise HookRegistrationError("Hook matcher contains unsupported events")
    if hook.timeout_seconds <= 0 or hook.timeout_seconds > _MAX_TIMEOUT_SECONDS:
        raise HookRegistrationError("Hook timeout is out of bounds")
    if hook.import_path or hook.shell_command:
        raise HookRegistrationError("Executable Hook definitions are not accepted")
    if not callable(hook.handler):
        raise HookRegistrationError("Hook handler is not callable")


def _execution_key(event: LifecycleEvent, hook: HookDefinition) -> str:
    """执行 execution key 的内部处理逻辑。

    Args:
        event: 需要处理的生命周期事件。
        hook: 待处理的生命周期钩子定义。
    """
    value = f"{event.idempotency_key}:{hook.name}:{hook.version}"
    return sha256(value.encode("utf-8")).hexdigest()


def _sanitize_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """执行 sanitize payload 的内部处理逻辑。

    Args:
        payload: 当前操作的结构化载荷。
    """
    safe = _sanitize_value(payload)
    if not isinstance(safe, dict):
        return {"value": safe}
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded.encode("utf-8")) <= _MAX_PAYLOAD_BYTES:
        return cast(dict[str, object], safe)
    return {
        "redacted": True,
        "reason": "payload_limit",
        "keys": sorted(str(key) for key in safe)[:64],
    }


def _sanitize_value(value: object, *, depth: int = 0) -> object:
    """执行 sanitize value 的内部处理逻辑。

    Args:
        value: 待校验、归一化或转换的输入值。
        depth: 用于执行当前操作的 depth 参数。
    """
    if depth > 4:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_value in list(value.items())[:64]:
            key = str(raw_key)
            normalized = key.casefold().replace("-", "_")
            if any(
                marker in normalized
                for marker in (
                    "authorization",
                    "cookie",
                    "api_key",
                    "apikey",
                    "token",
                    "secret",
                    "credential",
                    "password",
                    "prompt",
                    "transcript",
                    "raw_context",
                    "request_body",
                )
            ):
                continue
            result[key] = _sanitize_value(raw_value, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize_value(item, depth=depth + 1) for item in list(value)[:64]]
    if isinstance(value, str):
        return sanitize_text(value)[:_MAX_TEXT_BYTES]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(value)[:_MAX_TEXT_BYTES]


def _safe_reason(value: str) -> str:
    """执行 safe reason 的内部处理逻辑。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    return sanitize_text(value)[:500]


_DECISION_RANK: dict[HookDecision, int] = {
    "allow": 0,
    "require_approval": 1,
    "deny": 2,
}


__all__ = [
    "GuardDispatchResult",
    "HookDefinition",
    "HookExecutionResult",
    "HookMatcher",
    "HookRegistrationError",
    "LifecycleEvent",
    "LifecycleEventError",
    "LifecycleHookError",
    "LifecycleHookRegistry",
    "SUPPORTED_EVENT_TYPES",
    "build_lifecycle_event",
]
