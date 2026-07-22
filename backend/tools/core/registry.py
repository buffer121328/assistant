from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import json
from typing import Any, Literal, Protocol

from jsonschema.exceptions import SchemaError, best_match
from jsonschema.validators import validator_for
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runtime.budget import BudgetExceededError, RunBudget
from domain.models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    Task,
    ToolLog,
)
from common.redaction import sanitize_text

from policies.tool_approval import (
    EXACT_APPROVAL_TOOLS,
    external_approval_binding,
    external_audit_arguments,
)


ToolRiskLevel = Literal["L0", "L1", "L2", "L3", "L4"]

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

    task_id: str
    user_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    tool_snapshot_revision: int | None = None
    tool_version: str | None = None


class ToolHandler(Protocol):
    """表示 处理 tool handler 的后端数据结构或服务对象。"""

    async def __call__(self, invocation: ToolInvocation) -> Any:
        """将对象作为可调用逻辑执行。

        Args:
            invocation: invocation 参数。
        """
        ...


@dataclass(frozen=True)
class ToolSpec:
    """表示 处理 tool spec 的后端数据结构或服务对象。"""

    name: str
    description: str
    risk_level: ToolRiskLevel
    handler: ToolHandler
    enabled: bool = True
    handler_records_log: bool = False
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
        }
    )
    version: str = "static"
    source_id: str = "builtin"
    source_available: bool = True
    parallel_safe: bool = False
    requires_approval: bool = False
    timeout_seconds: float = 30.0
    max_retries: int = 0
    idempotent: bool = False
    supports_dry_run: bool = False
    compensation_tool: str | None = None
    required_permissions: tuple[str, ...] = ()


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
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            sensitive_values: sensitive_values 参数。
            snapshot_revision: snapshot_revision 参数。
        """
        self.session = session
        self.sensitive_values = tuple(sensitive_values)
        self.snapshot_revision = snapshot_revision
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
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=message,
            )
            raise ToolNotAllowedError(message)

        if not spec.source_available:
            message = f"Tool source is unavailable: {invocation.name}"
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
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=self._safe_json(
                    {"stop_reason": exc.stop_reason, "budget": exc.summary}
                ),
            )
            raise

        requires_approval = (
            invocation.name in approval_required_tools
            or spec.requires_approval
            or spec.risk_level in {"L3", "L4"}
        )
        if requires_approval and not await self._is_approved(invocation):
            await self._record(
                invocation=invocation,
                status="waiting_approval",
                output={"message": "Tool requires approval"},
                error=None,
            )
            raise ToolApprovalRequiredError(invocation.name)

        try:
            await self._guard_idempotent_retry(spec=spec, invocation=invocation)
        except ToolIdempotencyRequiredError as exc:
            await self._record(
                invocation=invocation,
                status="failed",
                output=None,
                error=str(exc),
            )
            raise

        try:
            result = await spec.handler(invocation)
        except Exception as exc:
            safe_error = self._safe_text(exc)
            if not spec.handler_records_log:
                await self._record(
                    invocation=invocation,
                    status="failed",
                    output=None,
                    error=safe_error,
                )
            raise ToolExecutionError(safe_error) from exc

        if not spec.handler_records_log:
            await self._record(
                invocation=invocation,
                status="succeeded",
                output=result,
                error=None,
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
                await self._record(
                    invocation=invocations[0],
                    status="failed",
                    output=None,
                    error=self._safe_json(
                        {"stop_reason": exc.stop_reason, "budget": exc.summary}
                    ),
                )
                raise

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
            or spec.risk_level in {"L3", "L4"}
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
        if spec.idempotent or spec.risk_level not in {"L3", "L4"}:
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

    async def _is_approved(self, invocation: ToolInvocation) -> bool:
        """执行 处理 is approved 的内部辅助逻辑。

        Args:
            invocation: invocation 参数。
        """
        subjects = (
            (external_approval_binding(invocation.name, invocation.arguments).subject,)
            if invocation.name in EXACT_APPROVAL_TOOLS
            else (invocation.name, "legacy.unknown")
        )
        approval_id = await self.session.scalar(
            select(Approval.id)
            .join(Task, Task.id == Approval.task_id)
            .where(
                Approval.task_id == invocation.task_id,
                Approval.tool_name == invocation.name,
                Approval.approval_type == ApprovalType.TOOL.value,
                Approval.subject.in_(subjects),
                Approval.status == ApprovalStatus.APPROVED.value,
                Task.user_id == invocation.user_id,
            )
            .limit(1)
        )
        return approval_id is not None

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
