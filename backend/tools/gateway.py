from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any, Mapping, Protocol, Sequence

from runtime.budget import RunBudget
from tools.core.catalog import ToolDescriptor
from tools.core.registry import ToolInvocation, ToolRegistry


@dataclass(frozen=True)
class ToolExecutionContext:
    """定义当前组件的职责和边界。"""

    task_id: str  # task_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    tenant_id: str | None = None  # tenant_id 对应的数据字段。
    organization_ids: tuple[str, ...] = ()  # organization_ids 对应的数据字段。
    credential_ref: str | None = None  # credential_ref 对应的数据字段。
    credential_payload: str | None = field(default=None, repr=False)  # credential_payload 对应的数据字段。


@dataclass(frozen=True)
class ToolResult:
    """定义当前组件的职责和边界。"""

    value: Any  # value 对应的数据字段。


class ToolProvider(Protocol):
    """定义当前组件的接口契约。"""

    async def discover(self) -> Sequence[ToolDescriptor]:
        """执行当前组件定义的业务处理逻辑。"""

    async def execute(self, tool: ToolDescriptor, arguments: Mapping[str, Any], context: ToolExecutionContext) -> ToolResult:
        """Execute one reviewed definition using trusted execution context.

        Args:
            tool: 用于执行当前操作的 tool 参数。
            arguments: 当前调用的结构化参数。
            context: 用于执行当前操作的 context 参数。
        """


class ToolProviderResolver(Protocol):
    """定义当前组件的接口契约。"""

    def resolve(self, tool_name: str) -> ToolProvider:
        """Return the provider or raise when no reviewed route exists.

        Args:
            tool_name: 用于执行当前操作的 tool name 参数。
        """


ProviderExecutor = Callable[[Mapping[str, Any], ToolExecutionContext], Awaitable[Any]]


class NativeToolProvider:
    """定义当前组件的职责和边界。"""

    def __init__(self, definitions: Sequence[ToolDescriptor], executors: Mapping[str, ProviderExecutor]) -> None:
        """Bind reviewed definitions to explicit in-process executor functions.

        Args:
            definitions: 用于执行当前操作的 definitions 参数。
            executors: 用于执行当前操作的 executors 参数。
        """
        self._definitions = tuple(definitions)
        self._executors = dict(executors)

    async def discover(self) -> tuple[ToolDescriptor, ...]:
        """执行当前组件定义的业务处理逻辑。"""
        return self._definitions

    async def execute(self, tool: ToolDescriptor, arguments: Mapping[str, Any], context: ToolExecutionContext) -> ToolResult:
        """Execute one native tool only when its reviewed route exists.

        Args:
            tool: 用于执行当前操作的 tool 参数。
            arguments: 当前调用的结构化参数。
            context: 用于执行当前操作的 context 参数。
        """
        try:
            executor = self._executors[tool.name]
        except KeyError as exc:
            raise LookupError(f"Native provider route is unavailable: {tool.name}") from exc
        return ToolResult(await executor(arguments, context))


class MCPProviderClient(Protocol):
    """定义当前组件的接口契约。"""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call one external MCP name using provider-owned transport state.

        Args:
            name: 目标对象或能力的名称。
            arguments: 当前调用的结构化参数。
        """


class MCPToolProvider:
    """定义当前组件的职责和边界。"""

    def __init__(self, definitions: Sequence[ToolDescriptor], external_names: Mapping[str, str], client: MCPProviderClient) -> None:
        """Bind reviewed definitions and mappings to an injected MCP client.

        Args:
            definitions: 用于执行当前操作的 definitions 参数。
            external_names: 用于执行当前操作的 external names 参数。
            client: 用于执行当前操作的 client 参数。
        """
        self._definitions = tuple(definitions)
        self._external_names = dict(external_names)
        self._client = client

    async def discover(self) -> tuple[ToolDescriptor, ...]:
        """执行当前组件定义的业务处理逻辑。"""
        return self._definitions

    async def execute(self, tool: ToolDescriptor, arguments: Mapping[str, Any], context: ToolExecutionContext) -> ToolResult:
        """Execute the provider-owned external name without exposing it to the agent.

        Args:
            tool: 用于执行当前操作的 tool 参数。
            arguments: 当前调用的结构化参数。
            context: 用于执行当前操作的 context 参数。
        """
        del context
        try:
            external_name = self._external_names[tool.name]
        except KeyError as exc:
            raise LookupError(f"MCP provider route is unavailable: {tool.name}") from exc
        return ToolResult(await self._client.call_tool(external_name, dict(arguments)))


class StaticToolProviderResolver:
    """定义当前组件的职责和边界。"""

    def __init__(self, routes: Mapping[str, ToolProvider]) -> None:
        """Copy trusted provider routes so callers cannot mutate them during execution.

        Args:
            routes: 用于执行当前操作的 routes 参数。
        """
        self._routes = dict(routes)

    def resolve(self, tool_name: str) -> ToolProvider:
        """Return one provider and fail closed when the route is absent.

        Args:
            tool_name: 用于执行当前操作的 tool name 参数。
        """
        try:
            return self._routes[tool_name]
        except KeyError as exc:
            raise LookupError(f"Tool provider route is unavailable: {tool_name}") from exc


class ToolGateway:
    """定义当前组件的职责和边界。"""

    def __init__(self, registry: ToolRegistry) -> None:
        """Reuse the established ToolRegistry governance invariants.

        Args:
            registry: 用于执行当前操作的 registry 参数。
        """
        self.registry = registry

    async def execute(self, invocation: ToolInvocation, *, allowed_tools: tuple[str, ...], approval_required_tools: tuple[str, ...], budget: RunBudget | None = None) -> Any:
        """Execute one invocation through the single governed registry path.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
            allowed_tools: 用于执行当前操作的 allowed tools 参数。
            approval_required_tools: 用于执行当前操作的 approval required tools 参数。
            budget: 用于执行当前操作的 budget 参数。
        """
        return await self.registry.execute(invocation, allowed_tools=allowed_tools, approval_required_tools=approval_required_tools, budget=budget)

    async def execute_batch(self, invocations: tuple[ToolInvocation, ...], *, allowed_tools: tuple[str, ...], approval_required_tools: tuple[str, ...], budget: RunBudget | None = None) -> tuple[Any, ...]:
        """Execute a bounded parallel batch through the same governance path.

        Args:
            invocations: 用于执行当前操作的 invocations 参数。
            allowed_tools: 用于执行当前操作的 allowed tools 参数。
            approval_required_tools: 用于执行当前操作的 approval required tools 参数。
            budget: 用于执行当前操作的 budget 参数。
        """
        return await self.registry.execute_batch(invocations, allowed_tools=allowed_tools, approval_required_tools=approval_required_tools, budget=budget)
