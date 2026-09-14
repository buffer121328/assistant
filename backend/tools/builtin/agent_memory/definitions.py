from __future__ import annotations

from typing import Any, Protocol, cast

from tools.core.catalog import ToolDescriptor
from tools.core.registry import ToolHandler, ToolInvocation, ToolRiskLevel, ToolSpec

from tools.builtin.agent_memory.constants import MEMORY_TOOL_VERSION

MEMORY_TOOL_DEFS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    (
        "memory.remember",
        "Store an explicit or candidate user memory through MemoryService",
        "L2",
        {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "memory_type": {"type": "string"},
                "source": {"type": "string"},
                "source_trust": {"type": "string"},
                "explicit": {"type": "boolean"},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    ),
    (
        "memory.recall",
        "Recall bounded owner-scoped memories with trace summary",
        "L1",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_items": {"type": "integer"},
                "token_budget": {"type": "integer"},
                "scope_kind": {"type": "string"},
                "scope_id": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    (
        "memory.forget",
        "Archive an owned memory by default",
        "L2",
        {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["memory_id"],
            "additionalProperties": False,
        },
    ),
)


class MemoryToolHandlers(Protocol):
    """定义记忆工具处理器必须提供的 remember、recall 和 forget 接口。"""
    async def remember(self, invocation: ToolInvocation) -> Any:
        """Handle memory.remember.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
        """
        ...

    async def recall(self, invocation: ToolInvocation) -> Any:
        """Handle memory.recall.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
        """
        ...

    async def forget(self, invocation: ToolInvocation) -> Any:
        """Handle memory.forget.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
        """
        ...


def build_memory_tool_descriptors(
    *, enabled: bool = True
) -> tuple[ToolDescriptor, ...]:
    """Build memory tool catalog descriptors.

    Args:
        enabled: 是否启用当前能力。
    """
    return tuple(
        ToolDescriptor(
            name=name,
            description=description,
            input_schema=schema,
            source_id="builtin",
            source_kind="builtin",
            version=MEMORY_TOOL_VERSION,
            enabled=enabled,
            risk_level=cast(ToolRiskLevel, risk),
            requires_approval=risk != "L1",
            tags=("memory", "agentic", "v10"),
            parallel_safe=risk == "L1",
        )
        for name, description, risk, schema in MEMORY_TOOL_DEFS
    )


def build_memory_tool_specs(service: MemoryToolHandlers) -> tuple[ToolSpec, ...]:
    """Build executable memory tool specs for a service instance.

    Args:
        service: 用于执行当前操作的 service 参数。
    """
    handlers = {
        "memory.remember": service.remember,
        "memory.recall": service.recall,
        "memory.forget": service.forget,
    }
    return tuple(
        ToolSpec(
            name=name,
            description=description,
            risk_level=cast(ToolRiskLevel, risk),
            handler=cast(ToolHandler, handlers[name]),
            handler_records_log=True,
            input_schema=schema,
            version=MEMORY_TOOL_VERSION,
            source_id="builtin",
            parallel_safe=risk == "L1",
        )
        for name, description, risk, schema in MEMORY_TOOL_DEFS
    )
