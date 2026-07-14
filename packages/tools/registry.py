from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import Approval, ApprovalStatus, Task, ToolLog
from packages.model_gateway import sanitize_text


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

    async def _is_approved(self, invocation: ToolInvocation) -> bool:
        approval_id = await self.session.scalar(
            select(Approval.id)
            .join(Task, Task.id == Approval.task_id)
            .where(
                Approval.task_id == invocation.task_id,
                Approval.tool_name == invocation.name,
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
                        "arguments": invocation.arguments,
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
