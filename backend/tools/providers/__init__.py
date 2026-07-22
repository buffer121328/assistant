"""External tool provider protocols and MCP adapters."""

from .base import CalendarProvider, EmailProvider
from .mcp import (
    MCPClientProtocol,
    MCPClientUnavailableError,
    MCPDiscoveryClientProtocol,
    MCPToolAdapter,
    MCPToolDescription,
    MCPToolSource,
)

__all__ = [
    "CalendarProvider",
    "EmailProvider",
    "MCPClientProtocol",
    "MCPClientUnavailableError",
    "MCPDiscoveryClientProtocol",
    "MCPToolAdapter",
    "MCPToolDescription",
    "MCPToolSource",
]
