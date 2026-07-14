from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .catalog import ToolDescriptor, ToolSourceKind
from .registry import ToolInvocation, ToolRiskLevel, ToolSpec


class MCPClientProtocol(Protocol):
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any: ...


class MCPDiscoveryClientProtocol(Protocol):
    async def list_tools(self) -> tuple[MCPToolDescription, ...]: ...


class MCPClientUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class MCPToolDescription:
    name: str
    description: str
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )
    risk_level: ToolRiskLevel = "L2"
    version: str = "1"
    tags: tuple[str, ...] = ()
    requires_approval: bool = False


class MCPToolSource:
    source_kind: ToolSourceKind = "mcp"

    def __init__(
        self,
        *,
        source_id: str,
        client: MCPDiscoveryClientProtocol | None,
    ) -> None:
        self.source_id = source_id
        self.client = client

    async def discover(self) -> tuple[ToolDescriptor, ...]:
        if self.client is None:
            return ()
        descriptions = await self.client.list_tools()
        return tuple(
            ToolDescriptor(
                name=description.name,
                description=description.description,
                input_schema=description.input_schema,
                source_id=self.source_id,
                source_kind="mcp",
                version=description.version,
                enabled=False,
                risk_level=description.risk_level,
                requires_approval=description.requires_approval,
                tags=description.tags,
            )
            for description in descriptions
        )


class MCPToolAdapter:
    def __init__(self, client: MCPClientProtocol | None = None) -> None:
        self.client = client

    def to_tool_spec(
        self,
        description: MCPToolDescription,
        *,
        enabled: bool = False,
    ) -> ToolSpec:
        async def call_mcp_tool(invocation: ToolInvocation) -> Any:
            if self.client is None:
                raise MCPClientUnavailableError("MCP client is not configured")
            return await self.client.call_tool(
                description.name,
                invocation.arguments,
            )

        return ToolSpec(
            name=description.name,
            description=description.description,
            risk_level=description.risk_level,
            handler=call_mcp_tool,
            enabled=enabled,
            input_schema=description.input_schema,
            version=description.version,
            source_id="mcp",
        )
