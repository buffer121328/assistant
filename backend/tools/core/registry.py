from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from hashlib import sha256
from typing import Any, Literal, Protocol

from jsonschema.exceptions import SchemaError, best_match
from jsonschema.validators import validator_for
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.lifecycle_hooks import LifecycleHookRegistry, build_lifecycle_event
from runtime.budget import BudgetExceededError, RunBudget
from domain.models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    AgentRun,
    GovernanceAudit,
    OrganizationMembership,
    Task,
    Tenant,
    ToolLog,
    utc_now,
)
from domain.policies.redaction import sanitize_text

from domain.policies.tool_approval import (
    EXACT_APPROVAL_TOOLS,
    external_approval_binding,
    external_audit_arguments,
)
from domain.policies.governance import (
    GovernanceDecision,
    GovernanceDecisionRecorder,
    GovernanceOutcome,
    NoopGovernanceDecisionRecorder,
)
from domain.policies.enterprise import (
    GovernedAgentProfile,
    GovernanceValidationError,
    PolicyDecision,
    PolicyEffect,
    normalize_tool_risk,
)


ToolRiskLevel = Literal["L0", "L1", "L2", "L3", "L4"]
GovernedToolRiskLevel = Literal["R0", "R1", "R2", "R3", "R4"]

MAX_AUDIT_TEXT_CHARS = 4_000


class ToolRegistryError(Exception):
    """表示 处理 tool registry error 的后端数据结构或服务对象。"""

    pass


class ToolNotAllowedError(ToolRegistryError):
    """表示 处理 tool not allowed error 的后端数据结构或服务对象。"""

    pass


class ToolSnapshotStaleError(ToolNotAllowedError):
    """表示 处理 tool snapshot stale error 的后端数据结构或服务对象。"""

    pass


class ToolSourceUnavailableError(ToolNotAllowedError):
    """表示 处理 tool source unavailable error 的后端数据结构或服务对象。"""

    pass


class ToolApprovalRequiredError(ToolRegistryError):
    """表示 处理 tool approval required error 的后端数据结构或服务对象。"""

    def __init__(self, tool_name: str) -> None:
        """初始化对象实例。

        Args:
            tool_name: tool_name 参数。
        """
        self.tool_name = tool_name
        super().__init__(f"Tool requires approval: {tool_name}")


class ToolArgumentsInvalidError(ToolRegistryError):
    """表示 处理 tool arguments invalid error 的后端数据结构或服务对象。"""

    def __init__(self, tool_name: str, detail: str) -> None:
        """初始化对象实例。

        Args:
            tool_name: tool_name 参数。
            detail: detail 参数。
        """
        self.tool_name = tool_name
        self.detail = detail
        super().__init__(f"Tool arguments are invalid: {tool_name} ({detail})")


class ToolIdempotencyRequiredError(ToolRegistryError, ValueError):
    """表示 处理 tool idempotency required error 的后端数据结构或服务对象。"""

    pass


class ToolExecutionError(ToolRegistryError):
    """表示 处理 tool execution error 的后端数据结构或服务对象。"""

    pass


@dataclass(frozen=True)
class ToolInvocation:
    """表示 处理 tool invocation 的后端数据结构或服务对象。"""

    task_id: str  # task_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    name: str  # name 对应的数据字段。
    arguments: dict[str, Any] = field(default_factory=dict)  # arguments 对应的数据字段。
    tool_snapshot_revision: int | None = None  # tool_snapshot_revision 对应的数据字段。
    tool_version: str | None = None  # tool_version 对应的数据字段。


class ToolHandler(Protocol):
    """表示 处理 tool handler 的后端数据结构或服务对象。"""

    async def __call__(self, invocation: ToolInvocation) -> Any:
        """将对象作为可调用逻辑执行。

        Args:
            invocation: invocation 参数。
        """
        ...


class ToolPolicyAuthorizer(Protocol):
    """定义当前组件的接口契约。"""

    async def authorize(
        self, invocation: ToolInvocation, *, risk_level: str
    ) -> PolicyDecision:
        """Return current four-outcome authorization for exact invocation data.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
            risk_level: 用于执行当前操作的 risk level 参数。
        """
        ...


@dataclass(frozen=True)
class ToolSpec:
    """表示 处理 tool spec 的后端数据结构或服务对象。"""

    name: str  # name 对应的数据字段。
    description: str  # description 对应的数据字段。
    risk_level: ToolRiskLevel | GovernedToolRiskLevel  # risk_level 对应的数据字段。
    handler: ToolHandler  # handler 对应的数据字段。
    enabled: bool = True  # enabled 对应的数据字段。
    handler_records_log: bool = False  # handler_records_log 对应的数据字段。
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
        }
    )  # input_schema 对应的数据字段。
    version: str = "static"  # version 对应的数据字段。
    source_id: str = "builtin"  # source_id 对应的数据字段。
    source_available: bool = True  # source_available 对应的数据字段。
    parallel_safe: bool = False  # parallel_safe 对应的数据字段。
    requires_approval: bool = False  # requires_approval 对应的数据字段。
    timeout_seconds: float = 30.0  # timeout_seconds 对应的数据字段。
    max_retries: int = 0  # max_retries 对应的数据字段。
    idempotent: bool = False  # idempotent 对应的数据字段。
    supports_dry_run: bool = False  # supports_dry_run 对应的数据字段。
    compensation_tool: str | None = None  # compensation_tool 对应的数据字段。
    required_permissions: tuple[str, ...] = ()  # required_permissions 对应的数据字段。


def _materialize_json_value(value: Any) -> Any:
    """执行 处理 materialize json value 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if isinstance(value, Mapping):
        return {str(key): _materialize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_materialize_json_value(item) for item in value]
    return value


class ToolRegistry:
    """表示 处理 tool registry 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        sensitive_values: Iterable[str | None] = (),
        snapshot_revision: int | None = None,
        decision_recorder: GovernanceDecisionRecorder | None = None,
        policy_authorizer: ToolPolicyAuthorizer | None = None,
        lifecycle_registry: LifecycleHookRegistry | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            sensitive_values: sensitive_values 参数。
            snapshot_revision: snapshot_revision 参数。
            lifecycle_registry: lifecycle_registry 参数。
        """
        self.session = session
        self.sensitive_values = tuple(sensitive_values)
        self.snapshot_revision = snapshot_revision
        self.decision_recorder = decision_recorder or NoopGovernanceDecisionRecorder()
        self.policy_authorizer = policy_authorizer
        self.lifecycle_registry = lifecycle_registry
        self._tools: dict[str, ToolSpec] = {}
        self._validators: dict[str, Any] = {}

    def register(self, spec: ToolSpec) -> None:
        """处理 register。

        Args:
            spec: spec 参数。
        """
        name = spec.name.strip()
        if not name:
            raise ValueError("Tool name must not be empty")
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        if spec.timeout_seconds <= 0:
            raise ValueError(f"Tool timeout_seconds must be positive: {name}")
        if spec.max_retries < 0:
            raise ValueError(f"Tool max_retries must not be negative: {name}")
        schema = _materialize_json_value(spec.input_schema)
        try:
            validator_type = validator_for(schema)
            validator_type.check_schema(schema)
        except SchemaError as exc:
            raise ValueError(f"Tool input schema is invalid: {name}") from exc
        self._tools[name] = spec
        self._validators[name] = validator_type(schema)

    @property
    def enabled_tool_names(self) -> tuple[str, ...]:
        """处理 enabled tool names。"""
        return tuple(name for name, spec in self._tools.items() if spec.enabled)

    async def execute(
        self,
        invocation: ToolInvocation,
        *,
        allowed_tools: tuple[str, ...],
        approval_required_tools: tuple[str, ...],
        budget: RunBudget | None = None,
    ) -> Any:
        """执行。

        Args:
            invocation: invocation 参数。
            allowed_tools: allowed_tools 参数。
            approval_required_tools: approval_required_tools 参数。
            budget: budget 参数。
        """
        spec = self._tools.get(invocation.name)
        if spec is None or not spec.enabled:
            message = f"Tool is not enabled: {invocation.name}"
            await self._emit_decision(
                invocation,
                outcome="deny",
                reason_code="tool_disabled",
            )
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=message,
            )
            raise ToolNotAllowedError(message)

        if not spec.source_available:
            message = f"Tool source is unavailable: {invocation.name}"
            await self._emit_decision(
                invocation,
                outcome="deny",
                reason_code="tool_source_unavailable",
            )
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=message,
            )
            raise ToolSourceUnavailableError(message)

        is_planned = (
            invocation.name in allowed_tools
            or invocation.name in approval_required_tools
        )
        if not is_planned:
            message = f"Tool is not allowed by execution plan: {invocation.name}"
            await self._emit_decision(
                invocation,
                outcome="deny",
                reason_code="tool_not_in_plan",
            )
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=message,
            )
            raise ToolNotAllowedError(message)

        if (
            invocation.tool_snapshot_revision is not None
            and self.snapshot_revision is not None
            and invocation.tool_snapshot_revision != self.snapshot_revision
        ):
            message = f"Tool snapshot is stale: {invocation.name}"
            await self._emit_decision(
                invocation,
                outcome="deny",
                reason_code="tool_snapshot_stale",
            )
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=message,
            )
            raise ToolSnapshotStaleError(message)

        if (
            invocation.tool_version is not None
            and invocation.tool_version != spec.version
        ):
            message = f"Tool version is stale: {invocation.name}"
            await self._emit_decision(
                invocation,
                outcome="deny",
                reason_code="tool_version_stale",
            )
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=message,
            )
            raise ToolSnapshotStaleError(message)

        try:
            self._validate_arguments(invocation)
        except ToolArgumentsInvalidError as exc:
            await self._emit_decision(
                invocation,
                outcome="deny",
                reason_code="tool_arguments_invalid",
            )
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=str(exc),
            )
            raise

        try:
            if budget is not None:
                budget.consume_tool_call(now=None)
        except BudgetExceededError as exc:
            await self._emit_decision(
                invocation,
                outcome="deny",
                reason_code="run_budget_exhausted",
            )
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=self._safe_json(
                    {"stop_reason": exc.stop_reason, "budget": exc.summary}
                ),
            )
            raise

        policy_decision = (
            await self.policy_authorizer.authorize(
                invocation,
                risk_level=normalize_tool_risk(spec.risk_level).value,
            )
            if self.policy_authorizer is not None
            else None
        )
        if policy_decision is not None and policy_decision.effect is PolicyEffect.DENY:
            message = f"Tool is denied by current policy: {invocation.name}"
            await self._emit_decision(
                invocation,
                outcome="deny",
                reason_code=policy_decision.reason_code,
                policy_effect=policy_decision.effect.value,
            )
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=message,
            )
            raise ToolNotAllowedError(message)

        requires_approval = (
            invocation.name in approval_required_tools
            or spec.requires_approval
            or normalize_tool_risk(spec.risk_level).value in {"R3", "R4"}
            or (
                policy_decision is not None
                and policy_decision.effect
                in {PolicyEffect.CONFIRM, PolicyEffect.APPROVAL}
            )
        )
        if self.lifecycle_registry is not None:
            task = await self.session.get(Task, invocation.task_id)
            if task is not None and task.user_id == invocation.user_id:
                guard_event = build_lifecycle_event(
                    event_type="before.tool",
                    actor_user_id=task.user_id,
                    tenant_id=task.tenant_id,
                    organization_id=task.organization_id,
                    conversation_id=task.conversation_id,
                    task_id=task.id,
                    payload={
                        "tool_name": invocation.name,
                        "risk_level": normalize_tool_risk(spec.risk_level).value,
                        "arguments_hash": external_approval_binding(
                            invocation.name, invocation.arguments
                        ).fingerprint,
                    },
                )
                guard_result = await self.lifecycle_registry.dispatch_guards(
                    guard_event
                )
                if guard_result.decision == "deny":
                    message = f"Tool denied by lifecycle Guard: {invocation.name}"
                    await self._emit_decision(
                        invocation,
                        outcome="deny",
                        reason_code="lifecycle_guard_denied",
                    )
                    await self._record(
                        invocation=invocation,
                        status="failed",
                        output=None,
                        error=message,
                    )
                    raise ToolNotAllowedError(message)
                if guard_result.decision == "require_approval":
                    requires_approval = True
        approved = (
            await self._approved_record(invocation) if requires_approval else None
        )
        if requires_approval and approved is None:
            await self._emit_decision(
                invocation,
                outcome="require_approval",
                reason_code="tool_approval_required",
                approval_fingerprint=self._approval_fingerprint(invocation),
                policy_effect=(
                    policy_decision.effect.value
                    if policy_decision is not None
                    else PolicyEffect.APPROVAL.value
                ),
            )
            await self._record(
                invocation=invocation,
                status="waiting_approval",
                output={"message": "Tool requires approval"},
                error=None,
            )
            await self._dispatch_lifecycle_observer(
                invocation,
                event_type="approval.requested",
                payload={
                    "tool_name": invocation.name,
                    "risk_level": normalize_tool_risk(spec.risk_level).value,
                    "reason": "tool_approval_required",
                },
            )
            raise ToolApprovalRequiredError(invocation.name)

        try:
            await self._guard_idempotent_retry(spec=spec, invocation=invocation)
        except ToolIdempotencyRequiredError as exc:
            await self._emit_decision(
                invocation,
                outcome="deny",
                reason_code="idempotency_key_required",
            )
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=str(exc),
            )
            raise

        await self._emit_decision(
            invocation,
            outcome="allow",
            reason_code="tool_authorized",
            approval_fingerprint=(
                self._approval_fingerprint(invocation) if requires_approval else None
            ),
            approval_id=approved.id if approved is not None else None,
            policy_effect=(
                policy_decision.effect.value
                if policy_decision is not None
                else PolicyEffect.ALLOW.value
            ),
        )

        try:
            result = await spec.handler(invocation)
        except Exception as exc:
            safe_error = self._safe_text(exc)
            await self._record_governance_audit(
                invocation,
                outcome="allow",
                reason_code="provider_failed",
                approval_fingerprint=(
                    self._approval_fingerprint(invocation)
                    if requires_approval
                    else None
                ),
                approval_id=approved.id if approved is not None else None,
                policy_effect=(
                    policy_decision.effect.value
                    if policy_decision is not None
                    else PolicyEffect.ALLOW.value
                ),
            )
            if not spec.handler_records_log:
                await self._record(
                    invocation=invocation,
                    status="failed",
                    output=None,
                    error=safe_error,
                )
            await self._dispatch_lifecycle_observer(
                invocation,
                event_type="tool.failed",
                payload={
                    "tool_name": invocation.name,
                    "risk_level": normalize_tool_risk(spec.risk_level).value,
                    "failure_class": type(exc).__name__[:64],
                },
            )
            raise ToolExecutionError(safe_error) from exc

        if not spec.handler_records_log:
            await self._record(
                invocation=invocation,
                status="succeeded",
                output=result,
                error=None,
            )
        await self._record_governance_audit(
            invocation,
            outcome="allow",
            reason_code="provider_succeeded",
            approval_fingerprint=(
                self._approval_fingerprint(invocation) if requires_approval else None
            ),
            approval_id=approved.id if approved is not None else None,
            policy_effect=(
                policy_decision.effect.value
                if policy_decision is not None
                else PolicyEffect.ALLOW.value
            ),
        )
        await self._dispatch_lifecycle_observer(
            invocation,
            event_type="tool.completed",
            payload={
                "tool_name": invocation.name,
                "risk_level": normalize_tool_risk(spec.risk_level).value,
                "approval_used": approved is not None,
            },
        )
        return result

    async def execute_batch(
        self,
        invocations: tuple[ToolInvocation, ...],
        *,
        allowed_tools: tuple[str, ...],
        approval_required_tools: tuple[str, ...],
        budget: RunBudget | None = None,
    ) -> tuple[Any, ...]:
        """执行 batch。

        Args:
            invocations: invocations 参数。
            allowed_tools: allowed_tools 参数。
            approval_required_tools: approval_required_tools 参数。
            budget: budget 参数。
        """
        if not invocations or len(invocations) > 3:
            raise ToolNotAllowedError("Tool batch size is invalid")

        specs: list[ToolSpec] = []
        for invocation in invocations:
            try:
                spec = self._validate_batch_invocation(
                    invocation,
                    allowed_tools=allowed_tools,
                    approval_required_tools=approval_required_tools,
                )
            except ToolRegistryError as exc:
                await self._emit_decision(
                    invocation,
                    outcome="deny",
                    reason_code=self._batch_error_reason_code(exc),
                )
                await self._record(
                    invocation=invocation,
                    status="failed",
                    output=None,
                    error=str(exc),
                )
                raise
            specs.append(spec)

        if budget is not None:
            try:
                budget.consume_tool_call(len(invocations), now=None)
            except BudgetExceededError as exc:
                for invocation in invocations:
                    await self._emit_decision(
                        invocation,
                        outcome="deny",
                        reason_code="run_budget_exhausted",
                    )
                    await self._record(
                        invocation=invocation,
                        status="failed",
                        output=None,
                        error=self._safe_json(
                            {"stop_reason": exc.stop_reason, "budget": exc.summary}
                        ),
                    )
                raise

        for invocation in invocations:
            await self._emit_decision(
                invocation,
                outcome="allow",
                reason_code="tool_authorized",
            )

        results = await asyncio.gather(
            *(
                spec.handler(invocation)
                for spec, invocation in zip(specs, invocations, strict=True)
            ),
            return_exceptions=True,
        )
        safe_results: list[Any] = []
        first_error: str | None = None
        for invocation, result in zip(invocations, results, strict=True):
            if isinstance(result, BaseException):
                safe_error = self._safe_text(result)
                first_error = first_error or safe_error
                await self._record(
                    invocation=invocation,
                    status="failed",
                    output=None,
                    error=safe_error,
                )
                safe_results.append({"error": safe_error})
            else:
                await self._record(
                    invocation=invocation,
                    status="succeeded",
                    output=result,
                    error=None,
                )
                safe_results.append(result)
        if first_error is not None:
            raise ToolExecutionError(f"Tool batch failed: {first_error}")
        return tuple(safe_results)

    def _validate_batch_invocation(
        self,
        invocation: ToolInvocation,
        *,
        allowed_tools: tuple[str, ...],
        approval_required_tools: tuple[str, ...],
    ) -> ToolSpec:
        """执行 校验 batch invocation 的内部辅助逻辑。

        Args:
            invocation: invocation 参数。
            allowed_tools: allowed_tools 参数。
            approval_required_tools: approval_required_tools 参数。
        """
        spec = self._tools.get(invocation.name)
        if spec is None or not spec.enabled:
            raise ToolNotAllowedError(f"Tool is not enabled: {invocation.name}")
        if not spec.source_available:
            raise ToolNotAllowedError(f"Tool source is unavailable: {invocation.name}")
        if (
            invocation.name not in allowed_tools
            or invocation.name in approval_required_tools
        ):
            raise ToolNotAllowedError(
                f"Tool is not allowed in a parallel batch: {invocation.name}"
            )
        if (
            spec.requires_approval
            or normalize_tool_risk(spec.risk_level).value in {"R3", "R4"}
            or not spec.parallel_safe
            or spec.handler_records_log
        ):
            raise ToolNotAllowedError(f"Tool is not parallel safe: {invocation.name}")
        if (
            invocation.tool_snapshot_revision is not None
            and self.snapshot_revision is not None
            and invocation.tool_snapshot_revision != self.snapshot_revision
        ):
            raise ToolNotAllowedError(f"Tool snapshot is stale: {invocation.name}")
        if (
            invocation.tool_version is not None
            and invocation.tool_version != spec.version
        ):
            raise ToolNotAllowedError(f"Tool version is stale: {invocation.name}")
        self._validate_arguments(invocation)
        return spec

    def _validate_arguments(self, invocation: ToolInvocation) -> None:
        """执行 校验 arguments 的内部辅助逻辑。

        Args:
            invocation: invocation 参数。
        """
        validator = self._validators[invocation.name]
        error = best_match(validator.iter_errors(invocation.arguments))
        if error is None:
            return
        path = ".".join(str(item) for item in error.absolute_path) or "arguments"
        detail = self._safe_text(f"{path}: {error.message}")
        raise ToolArgumentsInvalidError(invocation.name, detail)

    async def _guard_idempotent_retry(
        self, *, spec: ToolSpec, invocation: ToolInvocation
    ) -> None:
        """执行 处理 guard idempotent retry 的内部辅助逻辑。

        Args:
            spec: spec 参数。
            invocation: invocation 参数。
        """
        if spec.idempotent or normalize_tool_risk(spec.risk_level).value not in {
            "R3",
            "R4",
        }:
            return
        idempotency_key = invocation.arguments.get("idempotency_key")
        if isinstance(idempotency_key, str) and idempotency_key.strip():
            return
        previous = await self.session.scalar(
            select(ToolLog.id)
            .where(
                ToolLog.task_id == invocation.task_id,
                ToolLog.tool_name == invocation.name,
                ToolLog.status.in_(("succeeded", "waiting_approval")),
            )
            .limit(1)
        )
        if previous is not None:
            raise ToolIdempotencyRequiredError(
                f"High-risk non-idempotent retry requires idempotency_key: {invocation.name}"
            )

    async def _approved_record(self, invocation: ToolInvocation) -> Approval | None:
        """Return exact current approval evidence without legacy widening.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
        """
        task = await self.session.get(Task, invocation.task_id)
        if task is None or task.user_id != invocation.user_id:
            return None
        fingerprint = self._approval_fingerprint(invocation)
        binding = (
            external_approval_binding(invocation.name, invocation.arguments)
            if invocation.name in EXACT_APPROVAL_TOOLS
            else None
        )
        subjects = (
            (binding.subject,)
            if binding is not None
            else (
                invocation.name,
                f"{invocation.name}:{fingerprint}",
                "legacy.unknown",
            )
        )
        statement = select(Approval).where(
            Approval.task_id == invocation.task_id,
            Approval.tool_name == invocation.name,
            Approval.approval_type == ApprovalType.TOOL.value,
            Approval.subject.in_(subjects),
            Approval.status == ApprovalStatus.APPROVED.value,
        )
        if task.agent_profile_snapshot:
            current_run_id = await self.session.scalar(
                select(AgentRun.id)
                .where(AgentRun.task_id == task.id)
                .order_by(AgentRun.started_at.desc())
                .limit(1)
            )
            authority_revision = await self.session.scalar(
                select(Tenant.authority_revision).where(Tenant.id == task.tenant_id)
            )
            statement = statement.where(
                Approval.request_fingerprint == fingerprint,
                Approval.run_id == current_run_id,
                Approval.authority_revision == authority_revision,
                Approval.decided_by_user_id.is_not(None),
            )
        elif binding is not None:
            statement = statement.where(
                or_(
                    Approval.request_fingerprint == fingerprint,
                    Approval.request_fingerprint.is_(None),
                )
            )
        approval = await self.session.scalar(statement.limit(1))
        if approval is None:
            return None
        expiry = approval.expires_at
        if expiry is not None:
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= datetime.now(UTC):
                return None
        if approval.policy_decision == PolicyEffect.CONFIRM.value:
            return approval if approval.decided_by_user_id == task.user_id else None
        if approval.policy_decision == PolicyEffect.APPROVAL.value:
            if approval.decided_by_user_id == task.user_id:
                return None
            return (
                approval
                if await self._approval_actor_is_current(task, approval)
                else None
            )
        return approval

    async def _approval_actor_is_current(self, task: Task, approval: Approval) -> bool:
        """Recheck a delegated approver's current same-tenant authority.

        Args:
            task: 需要处理的任务对象。
            approval: 用于执行当前操作的 approval 参数。
        """
        actor_id = approval.decided_by_user_id
        if actor_id is None:
            return False
        enterprise_admin = await self.session.scalar(
            select(OrganizationMembership.id)
            .where(
                OrganizationMembership.tenant_id == task.tenant_id,
                OrganizationMembership.user_id == actor_id,
                OrganizationMembership.role == "enterprise_admin",
                OrganizationMembership.status == "active",
            )
            .limit(1)
        )
        if enterprise_admin is not None:
            return True
        if task.organization_id is None:
            return False
        department_admin = await self.session.scalar(
            select(OrganizationMembership.id)
            .where(
                OrganizationMembership.tenant_id == task.tenant_id,
                OrganizationMembership.organization_id == task.organization_id,
                OrganizationMembership.user_id == actor_id,
                OrganizationMembership.role == "department_admin",
                OrganizationMembership.status == "active",
            )
            .limit(1)
        )
        return department_admin is not None

    async def _dispatch_lifecycle_observer(
        self,
        invocation: ToolInvocation,
        *,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        """Publish a bounded post-decision Tool/Approval event without side effects.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
            event_type: 用于执行当前操作的 event type 参数。
            payload: 当前操作的结构化载荷。
        """
        if self.lifecycle_registry is None:
            return
        task = await self.session.get(Task, invocation.task_id)
        if task is None or task.user_id != invocation.user_id:
            return
        run_id = await self.session.scalar(
            select(AgentRun.id)
            .where(AgentRun.task_id == task.id)
            .order_by(AgentRun.started_at.desc())
            .limit(1)
        )
        try:
            event = build_lifecycle_event(
                event_type=event_type,
                actor_user_id=task.user_id,
                tenant_id=task.tenant_id,
                organization_id=task.organization_id,
                conversation_id=task.conversation_id,
                task_id=task.id,
                run_id=run_id,
                payload=payload,
            )
            await self.lifecycle_registry.dispatch_observers(event)
        except Exception:
            return

    async def _emit_decision(
        self,
        invocation: ToolInvocation,
        *,
        outcome: GovernanceOutcome,
        reason_code: str,
        approval_fingerprint: str | None = None,
        approval_id: str | None = None,
        policy_effect: str | None = None,
    ) -> None:
        """Record a bounded authorization decision without changing execution behavior.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
            outcome: 用于执行当前操作的 outcome 参数。
            reason_code: 用于执行当前操作的 reason code 参数。
            approval_fingerprint: 用于执行当前操作的 approval fingerprint 参数。
            approval_id: 用于执行当前操作的 approval id 参数。
            policy_effect: 用于执行当前操作的 policy effect 参数。
        """
        await self._record_governance_audit(
            invocation,
            outcome=outcome,
            reason_code=reason_code,
            approval_fingerprint=approval_fingerprint,
            approval_id=approval_id,
            policy_effect=policy_effect,
        )
        try:
            await self.decision_recorder.record(
                task_id=invocation.task_id,
                user_id=invocation.user_id,
                decision=GovernanceDecision(
                    outcome=outcome,
                    reason_code=reason_code,
                    tool_name=invocation.name,
                    tool_snapshot_revision=invocation.tool_snapshot_revision,
                    tool_version=invocation.tool_version,
                    approval_fingerprint=approval_fingerprint,
                ),
            )
        except Exception:
            # Auditing must not weaken or interrupt the existing execution boundary.
            return

    async def _record_governance_audit(
        self,
        invocation: ToolInvocation,
        *,
        outcome: GovernanceOutcome,
        reason_code: str,
        approval_fingerprint: str | None,
        approval_id: str | None,
        policy_effect: str | None = None,
    ) -> None:
        """Persist mandatory redacted governance facts before protected execution.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
            outcome: 用于执行当前操作的 outcome 参数。
            reason_code: 用于执行当前操作的 reason code 参数。
            approval_fingerprint: 用于执行当前操作的 approval fingerprint 参数。
            approval_id: 用于执行当前操作的 approval id 参数。
            policy_effect: 用于执行当前操作的 policy effect 参数。
        """
        if not isinstance(self.session, AsyncSession):
            return
        task = await self.session.get(Task, invocation.task_id)
        if task is None or task.user_id != invocation.user_id:
            return
        run_id = await self.session.scalar(
            select(AgentRun.id)
            .where(AgentRun.task_id == task.id)
            .order_by(AgentRun.started_at.desc())
            .limit(1)
        )
        spec = self._tools.get(invocation.name)
        capability_key = None
        if approval_id is not None:
            capability_key = await self.session.scalar(
                select(Approval.capability_key).where(Approval.id == approval_id)
            )
        if capability_key is None and task.agent_profile_snapshot:
            try:
                profile = GovernedAgentProfile.from_snapshot(
                    task.agent_profile_snapshot
                )
            except GovernanceValidationError:
                profile = None
            if (
                profile is not None
                and invocation.name in profile.tools
                and len(profile.capabilities) == 1
            ):
                capability_key = profile.capabilities[0].split("@", 1)[0]
        encoded = json.dumps(
            invocation.arguments,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
        self.session.add(
            GovernanceAudit(
                tenant_id=task.tenant_id,
                subject_id=task.user_id,
                organization_id=task.organization_id,
                task_id=task.id,
                run_id=run_id,
                agent_id=task.agent_profile_schema_version,
                capability_key=capability_key,
                tool_key=invocation.name,
                provider_key=spec.source_id if spec is not None else None,
                resource_type="tool",
                resource_id=invocation.name,
                risk=normalize_tool_risk(spec.risk_level).value
                if spec is not None
                else None,
                policy_decision=policy_effect or outcome,
                approval_id=approval_id,
                arguments_hash=approval_fingerprint
                or sha256(encoded.encode("utf-8")).hexdigest(),
                result_status=reason_code,
                summary="",
                created_at=utc_now(),
            )
        )
        await self.session.flush()

    def _approval_fingerprint(self, invocation: ToolInvocation) -> str | None:
        """Bind every governed approval to canonical exact tool arguments.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
        """
        if invocation.name in EXACT_APPROVAL_TOOLS:
            return external_approval_binding(
                invocation.name, invocation.arguments
            ).fingerprint
        encoded = json.dumps(
            invocation.arguments,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
        return sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _batch_error_reason_code(error: ToolRegistryError) -> str:
        """将批量工具校验异常映射为稳定的审计原因码。

        Args:
            error: 用于执行当前操作的 error 参数。
        """
        if isinstance(error, ToolArgumentsInvalidError):
            return "tool_arguments_invalid"
        return "tool_batch_validation_failed"

    async def _record(
        self,
        *,
        invocation: ToolInvocation,
        status: str,
        output: Any,
        error: str | None,
    ) -> None:
        """执行 记录 的内部辅助逻辑。

        Args:
            invocation: invocation 参数。
            status: status 参数。
            output: output 参数。
            error: error 参数。
        """
        arguments = (
            external_audit_arguments(invocation.name, invocation.arguments)
            if invocation.name in EXACT_APPROVAL_TOOLS
            else invocation.arguments
        )
        self.session.add(
            ToolLog(
                task_id=invocation.task_id,
                tool_name=invocation.name,
                status=status,
                input_text=self._safe_json(
                    {
                        "tool": invocation.name,
                        "task_id": invocation.task_id,
                        "user_id": invocation.user_id,
                        "arguments": arguments,
                        "tool_snapshot_revision": invocation.tool_snapshot_revision,
                        "tool_version": invocation.tool_version,
                    }
                ),
                output_text=(self._safe_json(output) if output is not None else None),
                error_message=(self._safe_text(error) if error else None),
            )
        )
        await self.session.flush()

    def _safe_json(self, value: Any) -> str:
        """执行 处理 safe json 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        return self._safe_text(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            )
        )

    def _safe_text(self, value: object) -> str:
        """执行 处理 safe text 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        text = sanitize_text(value, extra_sensitive_values=self.sensitive_values)
        if "traceback" in text.lower():
            text = "内部错误已脱敏"
        if len(text) <= MAX_AUDIT_TEXT_CHARS:
            return text
        marker = "...[truncated]"
        return text[: MAX_AUDIT_TEXT_CHARS - len(marker)] + marker


def build_search_tool_spec(
    search_tool: Any,
    *,
    version: str = "builtin-search-v1",
    source_id: str = "builtin",
    source_available: bool = True,
) -> ToolSpec:
    """构建 search tool spec。

    Args:
        search_tool: search_tool 参数。
        version: version 参数。
        source_id: source_id 参数。
        source_available: source_available 参数。
    """

    async def search_handler(invocation: ToolInvocation) -> Any:
        """搜索 handler。

        Args:
            invocation: invocation 参数。
        """
        return await search_tool.search(
            task_id=invocation.task_id,
            user_id=invocation.user_id,
            query=str(invocation.arguments.get("query") or ""),
        )

    return ToolSpec(
        name="search.web",
        description="Search public web sources",
        risk_level="L2",
        handler=search_handler,
        handler_records_log=True,
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Public web search query",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        version=version,
        source_id=source_id,
        source_available=source_available,
    )
