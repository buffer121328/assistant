from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .catalog import ToolDescriptor, ToolSourceKind
from .registry import ToolInvocation, ToolRiskLevel, ToolSpec


class MCPClientProtocol(Protocol):
    """表示 处理 mcpclient protocol 的后端数据结构或服务对象。"""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """处理 call tool。

        Args:
            name: name 参数。
            arguments: arguments 参数。
        """
        ...


class MCPDiscoveryClientProtocol(Protocol):
    """表示 处理 mcpdiscovery client protocol 的后端数据结构或服务对象。"""

    async def list_tools(self) -> tuple[MCPToolDescription, ...]:
        """列出 tools。"""
        ...


class MCPClientUnavailableError(Exception):
    """表示 处理 mcpclient unavailable error 的后端数据结构或服务对象。"""

    pass


@dataclass(frozen=True)
class MCPToolDescription:
    """表示 处理 mcptool description 的后端数据结构或服务对象。"""

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
    """表示 处理 mcptool source 的后端数据结构或服务对象。"""

    source_kind: ToolSourceKind = "mcp"

    def __init__(
        self,
        *,
        source_id: str,
        client: MCPDiscoveryClientProtocol | None,
    ) -> None:
        """初始化对象实例。

        Args:
            source_id: source_id 参数。
            client: client 参数。
        """
        self.source_id = source_id
        self.client = client

    async def discover(self) -> tuple[ToolDescriptor, ...]:
        """处理 discover。"""
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
    """表示 处理 mcptool adapter 的后端数据结构或服务对象。"""

    def __init__(self, client: MCPClientProtocol | None = None) -> None:
        """初始化对象实例。

        Args:
            client: client 参数。
        """
        self.client = client

    def to_tool_spec(
        self,
        description: MCPToolDescription,
        *,
        enabled: bool = False,
    ) -> ToolSpec:
        """转换为目标格式 tool spec。

        Args:
            description: description 参数。
            enabled: enabled 参数。
        """

        async def call_mcp_tool(invocation: ToolInvocation) -> Any:
            """处理 call mcp tool。

            Args:
                invocation: invocation 参数。
            """
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
