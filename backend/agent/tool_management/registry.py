from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
import json
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    Task,
    ToolLog,
)
from model_gateway import sanitize_text

from .approval import EXACT_APPROVAL_TOOLS, external_approval_binding, external_audit_arguments


ToolRiskLevel = Literal["L1", "L2", "L3"]


class ToolRegistryError(Exception):
    pass


class ToolNotAllowedError(ToolRegistryError):
    pass


class ToolSnapshotStaleError(ToolNotAllowedError):
    pass


class ToolSourceUnavailableError(ToolNotAllowedError):
    pass


class ToolApprovalRequiredError(ToolRegistryError):
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Tool requires approval: {tool_name}")


class ToolExecutionError(ToolRegistryError):
    pass


@dataclass(frozen=True)
class ToolInvocation:
    task_id: str
    user_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    tool_snapshot_revision: int | None = None
    tool_version: str | None = None


class ToolHandler(Protocol):
    async def __call__(self, invocation: ToolInvocation) -> Any: ...


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk_level: ToolRiskLevel
    handler: ToolHandler
    enabled: bool = True
    handler_records_log: bool = False
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )
    version: str = "static"
    source_id: str = "builtin"
    source_available: bool = True
    parallel_safe: bool = False


class ToolRegistry:
    def __init__(
        self,
        *,
        session: AsyncSession,
        sensitive_values: Iterable[str | None] = (),
        snapshot_revision: int | None = None,
    ) -> None:
        self.session = session
        self.sensitive_values = tuple(sensitive_values)
        self.snapshot_revision = snapshot_revision
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        name = spec.name.strip()
        if not name:
            raise ValueError("Tool name must not be empty")
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = spec

    @property
    def enabled_tool_names(self) -> tuple[str, ...]:
        return tuple(name for name, spec in self._tools.items() if spec.enabled)

    async def execute(
        self,
        invocation: ToolInvocation,
        *,
        allowed_tools: tuple[str, ...],
        approval_required_tools: tuple[str, ...],
    ) -> Any:
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

        plan_requires_approval = invocation.name in approval_required_tools
        if plan_requires_approval:
            if not await self._is_approved(invocation):
                await self._record(
                    invocation=invocation,
                    status="waiting_approval",
                    output={"message": "Tool requires approval"},
                    error=None,
                )
                raise ToolApprovalRequiredError(invocation.name)
        if (
            spec.risk_level == "L3"
            and not plan_requires_approval
            and not await self._is_approved(invocation)
        ):
            await self._record(
                invocation=invocation,
                status="waiting_approval",
                output={"message": "L3 tool requires approval"},
                error=None,
            )
            raise ToolApprovalRequiredError(invocation.name)

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
    ) -> tuple[Any, ...]:
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
            except ToolNotAllowedError as exc:
                await self._record(
                    invocation=invocation,
                    status="failed",
                    output=None,
                    error=str(exc),
                )
                raise
            specs.append(spec)

        results = await asyncio.gather(
            *(spec.handler(invocation) for spec, invocation in zip(specs, invocations, strict=True)),
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
        spec = self._tools.get(invocation.name)
        if spec is None or not spec.enabled:
            raise ToolNotAllowedError(f"Tool is not enabled: {invocation.name}")
        if not spec.source_available:
            raise ToolNotAllowedError(f"Tool source is unavailable: {invocation.name}")
        if invocation.name not in allowed_tools or invocation.name in approval_required_tools:
            raise ToolNotAllowedError(f"Tool is not allowed in a parallel batch: {invocation.name}")
        if spec.risk_level == "L3" or not spec.parallel_safe or spec.handler_records_log:
            raise ToolNotAllowedError(f"Tool is not parallel safe: {invocation.name}")
        if (
            invocation.tool_snapshot_revision is not None
            and self.snapshot_revision is not None
            and invocation.tool_snapshot_revision != self.snapshot_revision
        ):
            raise ToolNotAllowedError(f"Tool snapshot is stale: {invocation.name}")
        if invocation.tool_version is not None and invocation.tool_version != spec.version:
            raise ToolNotAllowedError(f"Tool version is stale: {invocation.name}")
        return spec

    async def _is_approved(self, invocation: ToolInvocation) -> bool:
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
        text = sanitize_text(value, extra_sensitive_values=self.sensitive_values)
        if "traceback" in text.lower():
            return "内部错误已脱敏"
        return text


def build_search_tool_spec(
    search_tool: Any,
    *,
    version: str = "builtin-search-v1",
    source_id: str = "builtin",
    source_available: bool = True,
) -> ToolSpec:
    async def search_handler(invocation: ToolInvocation) -> Any:
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
