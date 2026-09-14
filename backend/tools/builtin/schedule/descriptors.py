from __future__ import annotations

from typing import Any, cast

from tools.core.catalog import ToolDescriptor
from tools.core.registry import ToolInvocation, ToolRiskLevel, ToolSpec

from .constants import MAX_HISTORY, SCHEDULE_TOOL_VERSION
from .payloads import _optional_int, _optional_str, _parse_datetime
from .service import AgentScheduleService


def build_schedule_tool_descriptors(
    *, enabled: bool = True
) -> tuple[ToolDescriptor, ...]:
    """构建 schedule tool descriptors。

    Args:
        enabled: enabled 参数。
    """
    return tuple(
        ToolDescriptor(
            name=name,
            description=description,
            input_schema=schema,
            source_id="builtin",
            source_kind="builtin",
            version=SCHEDULE_TOOL_VERSION,
            enabled=enabled,
            risk_level=cast(ToolRiskLevel, risk),
            requires_approval=risk != "L1",
            tags=("schedule", "automation", "v10"),
            parallel_safe=risk == "L1",
        )
        for name, description, risk, schema in _SCHEDULE_TOOL_DEFS
    )


def build_schedule_tool_specs(service: AgentScheduleService) -> tuple[ToolSpec, ...]:
    """构建 schedule tool specs。

    Args:
        service: service 参数。
    """

    async def create(invocation: ToolInvocation) -> Any:
        """创建。

        Args:
            invocation: invocation 参数。
        """
        schedule = await service.create(
            user_id=invocation.user_id,
            mode=str(invocation.arguments.get("mode") or ""),
            payload=dict(invocation.arguments.get("payload") or {}),
            conversation_id=_optional_str(invocation.arguments.get("conversation_id")),
            run_at=_parse_datetime(invocation.arguments.get("run_at")),
            every_seconds=_optional_int(invocation.arguments.get("every_seconds")),
            cron_expr=_optional_str(invocation.arguments.get("cron_expr")),
            timezone=str(invocation.arguments.get("timezone") or "UTC"),
            catch_up_policy=str(invocation.arguments.get("catch_up_policy") or "skip"),
        )
        return service._schedule_dict(schedule)

    async def list_schedules(invocation: ToolInvocation) -> Any:
        """列出 schedules。

        Args:
            invocation: invocation 参数。
        """
        return await service.list_schedules(user_id=invocation.user_id)

    async def toggle(invocation: ToolInvocation) -> Any:
        """处理 toggle。

        Args:
            invocation: invocation 参数。
        """
        return await service.toggle(
            user_id=invocation.user_id,
            schedule_id=str(invocation.arguments.get("schedule_id") or ""),
            enabled=bool(invocation.arguments.get("enabled")),
        )

    async def run_now(invocation: ToolInvocation) -> Any:
        """运行 now。

        Args:
            invocation: invocation 参数。
        """
        return await service.run_now(
            user_id=invocation.user_id,
            schedule_id=str(invocation.arguments.get("schedule_id") or ""),
        )

    async def delete(invocation: ToolInvocation) -> Any:
        """删除。

        Args:
            invocation: invocation 参数。
        """
        return await service.delete(
            user_id=invocation.user_id,
            schedule_id=str(invocation.arguments.get("schedule_id") or ""),
        )

    async def history(invocation: ToolInvocation) -> Any:
        """处理 history。

        Args:
            invocation: invocation 参数。
        """
        return await service.history(
            user_id=invocation.user_id,
            schedule_id=str(invocation.arguments.get("schedule_id") or ""),
            limit=int(invocation.arguments.get("limit") or MAX_HISTORY),
        )

    handlers = {
        "schedule.create": create,
        "schedule.list": list_schedules,
        "schedule.toggle": toggle,
        "schedule.run_now": run_now,
        "schedule.delete": delete,
        "schedule.history": history,
    }
    return tuple(
        ToolSpec(
            name=name,
            description=description,
            risk_level=cast(ToolRiskLevel, risk),
            handler=handlers[name],
            input_schema=schema,
            version=SCHEDULE_TOOL_VERSION,
            source_id="builtin",
        )
        for name, description, risk, schema in _SCHEDULE_TOOL_DEFS
    )  # type: ignore[arg-type]


_ID = {"type": "string", "minLength": 1}
_PAYLOAD = {
    "type": "object",
    "properties": {
        "task_type": {"type": "string"},
        "input_text": {"type": "string"},
        "workflow_key": {"type": "string"},
        "model_class": {"type": "string"},
    },
    "required": ["input_text"],
    "additionalProperties": False,
}
_SCHEDULE_TOOL_DEFS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    (
        "schedule.create",
        "Create an at/every/cron schedule",
        "L2",
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["at", "every", "cron"]},
                "payload": _PAYLOAD,
                "conversation_id": {"type": "string"},
                "run_at": {"type": "string"},
                "every_seconds": {"type": "integer"},
                "cron_expr": {"type": "string"},
                "timezone": {"type": "string"},
                "catch_up_policy": {"type": "string", "enum": ["skip", "catch_up"]},
            },
            "required": ["mode", "payload"],
            "additionalProperties": False,
        },
    ),
    (
        "schedule.list",
        "List owned schedules",
        "L1",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    (
        "schedule.toggle",
        "Enable or pause an owned schedule",
        "L2",
        {
            "type": "object",
            "properties": {"schedule_id": _ID, "enabled": {"type": "boolean"}},
            "required": ["schedule_id", "enabled"],
            "additionalProperties": False,
        },
    ),
    (
        "schedule.run_now",
        "Materialize an owned schedule immediately",
        "L2",
        {
            "type": "object",
            "properties": {"schedule_id": _ID},
            "required": ["schedule_id"],
            "additionalProperties": False,
        },
    ),
    (
        "schedule.delete",
        "Delete an owned schedule",
        "L2",
        {
            "type": "object",
            "properties": {"schedule_id": _ID},
            "required": ["schedule_id"],
            "additionalProperties": False,
        },
    ),
    (
        "schedule.history",
        "Read bounded owned schedule run history",
        "L1",
        {
            "type": "object",
            "properties": {"schedule_id": _ID, "limit": {"type": "integer"}},
            "required": ["schedule_id"],
            "additionalProperties": False,
        },
    ),
)
