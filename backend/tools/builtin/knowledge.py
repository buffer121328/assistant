from __future__ import annotations

from dataclasses import asdict

from rag import KnowledgeService

from tools.core.catalog import ToolDescriptor
from tools.core.registry import ToolInvocation, ToolSpec


def build_knowledge_tool_descriptor() -> ToolDescriptor:
    """构建 knowledge tool descriptor。"""
    return ToolDescriptor(
        name="knowledge.search",
        description="Search the current user's indexed personal documents",
        input_schema=_schema(),
        source_id="builtin",
        source_kind="builtin",
        version="knowledge-v1",
        enabled=True,
        risk_level="L1",
        requires_approval=False,
        tags=("learn", "daily", "office"),
        parallel_safe=True,
    )


def build_knowledge_tool_spec(service: KnowledgeService) -> ToolSpec:
    """构建 knowledge tool spec。

    Args:
        service: service 参数。
    """

    async def search(invocation: ToolInvocation) -> list[dict[str, object]]:
        """搜索。

        Args:
            invocation: invocation 参数。
        """
        results = await service.search(
            user_id=invocation.user_id,
            query=str(invocation.arguments["query"]),
            limit=int(invocation.arguments.get("limit", 5)),
        )
        return [asdict(result) for result in results]

    return ToolSpec(
        name="knowledge.search",
        description="Search the current user's indexed personal documents",
        risk_level="L1",
        handler=search,
        input_schema=_schema(),
        version="knowledge-v1",
        parallel_safe=True,
    )


def _schema() -> dict[str, object]:
    """执行 处理 schema 的内部辅助逻辑。"""
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
