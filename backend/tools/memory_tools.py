from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from memory.agentic import classify_memory_query_type, rrf_weights_for_query
from memory.retrieval import RetrievalWeights, retrieve_memories
from memory.safety import classify_memory_sensitivity
from memory.semantic import SemanticMemory
from domain.services import (
    ForbiddenMemoryContentError,
    MemoryNotFoundError,
    MemoryService,
)
from domain.models import ToolLog
from models import sanitize_text

from .catalog import ToolDescriptor
from .registry import ToolInvocation, ToolRiskLevel, ToolSpec

MEMORY_TOOL_VERSION = "v10-memory-tools-v1"


@dataclass
class AgentMemoryToolService:
    """表示 处理 agent memory tool service 的后端数据结构或服务对象。"""

    session: AsyncSession
    semantic_memory: SemanticMemory | None = None
    max_items: int = 5
    token_budget: int = 400

    async def remember(self, invocation: ToolInvocation) -> dict[str, object]:
        """处理 remember。

        Args:
            invocation: invocation 参数。
        """
        content = str(invocation.arguments.get("content") or "")
        source = str(invocation.arguments.get("source") or "user_explicit")
        memory_type = str(invocation.arguments.get("memory_type") or "preference")
        explicit = bool(invocation.arguments.get("explicit", source == "user_explicit"))
        source_trust = str(
            invocation.arguments.get("source_trust")
            or ("trusted_user" if explicit else "untrusted_external")
        )
        if source_trust != "trusted_user":
            explicit = False
        safety = classify_memory_sensitivity(content)
        if safety.sensitivity == "forbidden":
            await self._audit(
                invocation,
                "memory.remember",
                "failed",
                {"reason": safety.reason_code},
                None,
            )
            raise ForbiddenMemoryContentError("Memory content is forbidden")
        service = MemoryService(self.session, semantic_memory=self.semantic_memory)
        memory = await service.create_memory(
            user_id=invocation.user_id,
            content=content,
            memory_type=memory_type,
            source_kind=source,
            source_trust=source_trust,
            reason_code="explicit_user_request"
            if explicit
            else "agent_inferred_candidate",
            source_task_id=invocation.task_id,
            confirmed_by_user=explicit,
        )
        await self._audit(
            invocation,
            "memory.remember",
            "succeeded",
            {
                "memory_id": memory.id,
                "status": memory.status,
                "sensitivity": memory.sensitivity,
            },
            None,
        )
        return {
            "memory_id": memory.id,
            "status": memory.status,
            "sensitivity": memory.sensitivity,
            "source_kind": memory.source_kind,
            "confirmed_by_user": memory.confirmed_by_user,
        }

    async def recall(self, invocation: ToolInvocation) -> dict[str, object]:
        """处理 recall。

        Args:
            invocation: invocation 参数。
        """
        query = str(invocation.arguments.get("query") or "")
        query_type = classify_memory_query_type(query)
        max_items = _positive_int(invocation.arguments.get("max_items"), self.max_items)
        token_budget = _positive_int(
            invocation.arguments.get("token_budget"), self.token_budget
        )
        weights = RetrievalWeights(max_items=max_items, token_budget=token_budget)
        result = await retrieve_memories(
            session=self.session,
            user_id=invocation.user_id,
            query=query,
            semantic_memory=self.semantic_memory,
            weights=weights,
            task_id=invocation.task_id,
            scope_kind=str(invocation.arguments.get("scope_kind") or "user/global"),
            scope_id=cast(str | None, invocation.arguments.get("scope_id")),
        )
        payload: dict[str, object] = {
            "items": [
                {
                    "memory_id": item.memory_id,
                    "content": item.content,
                    "memory_type": item.memory_type,
                    "score": item.score,
                    "historical": item.historical,
                    "tokens": item.injected_tokens,
                }
                for item in result.items
            ],
            "trace": {
                "trace_id": result.trace_id,
                "query_type": query_type,
                "time_intent": result.time_intent,
                "retrieval_mode": "fallback"
                if "fallback" in result.mode
                else result.mode,
                "raw_mode": result.mode,
                "injected_tokens": result.injected_tokens,
                "max_items": max_items,
                "token_budget": token_budget,
                "rrf_weights": rrf_weights_for_query(query),
            },
        }
        await self._audit(
            invocation,
            "memory.recall",
            "succeeded",
            {
                "count": len(result.items),
                "trace_id": result.trace_id,
                "query_type": query_type,
                "mode": "fallback" if "fallback" in result.mode else result.mode,
            },
            None,
        )
        return payload

    async def forget(self, invocation: ToolInvocation) -> dict[str, object]:
        """处理 forget。

        Args:
            invocation: invocation 参数。
        """
        memory_id = str(invocation.arguments.get("memory_id") or "")
        reason = str(invocation.arguments.get("reason") or "user_requested_forget")[
            :500
        ]
        service = MemoryService(self.session, semantic_memory=self.semantic_memory)
        try:
            memory = await service.get_memory(
                user_id=invocation.user_id, memory_id=memory_id
            )
        except MemoryNotFoundError:
            await self._audit(
                invocation,
                "memory.forget",
                "failed",
                {"memory_id": memory_id, "reason": "not_owned_or_missing"},
                None,
            )
            raise
        memory.status = "archived"
        memory.is_active = False
        memory.archived_at = memory.archived_at or memory.updated_at
        await service.add_feedback(
            user_id=invocation.user_id,
            memory_id=memory.id,
            feedback_type="forgotten",
            task_id=invocation.task_id,
        )
        await self.session.flush()
        await self._audit(
            invocation,
            "memory.forget",
            "succeeded",
            {"memory_id": memory.id, "status": memory.status, "reason": reason},
            None,
        )
        return {"memory_id": memory.id, "status": memory.status, "archived": True}

    async def _audit(
        self,
        invocation: ToolInvocation,
        tool_name: str,
        status: str,
        output: object | None,
        error: str | None,
    ) -> None:
        """执行 处理 audit 的内部辅助逻辑。

        Args:
            invocation: invocation 参数。
            tool_name: tool_name 参数。
            status: status 参数。
            output: output 参数。
            error: error 参数。
        """
        self.session.add(
            ToolLog(
                task_id=invocation.task_id,
                tool_name=tool_name,
                status=status,
                input_text=sanitize_text(
                    {
                        "tool": tool_name,
                        "task_id": invocation.task_id,
                        "user_id": invocation.user_id,
                        "arguments": _safe_arguments(invocation.arguments),
                    }
                ),
                output_text=sanitize_text(output) if output is not None else None,
                error_message=sanitize_text(error) if error else None,
            )
        )
        await self.session.flush()


def build_memory_tool_descriptors(
    *, enabled: bool = True
) -> tuple[ToolDescriptor, ...]:
    """构建 memory tool descriptors。

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
            version=MEMORY_TOOL_VERSION,
            enabled=enabled,
            risk_level=cast(ToolRiskLevel, risk),
            requires_approval=risk != "L1",
            tags=("memory", "agentic", "v10"),
            parallel_safe=risk == "L1",
        )
        for name, description, risk, schema in _MEMORY_TOOL_DEFS
    )


def build_memory_tool_specs(service: AgentMemoryToolService) -> tuple[ToolSpec, ...]:
    """构建 memory tool specs。

    Args:
        service: service 参数。
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
            handler=handlers[name],
            handler_records_log=True,
            input_schema=schema,
            version=MEMORY_TOOL_VERSION,
            source_id="builtin",
            parallel_safe=risk == "L1",
        )
        for name, description, risk, schema in _MEMORY_TOOL_DEFS
    )


def _positive_int(value: object, default: int) -> int:
    """执行 处理 positive int 的内部辅助逻辑。

    Args:
        value: value 参数。
        default: default 参数。
    """
    try:
        parsed = (
            int(value) if isinstance(value, int | str | bytes | bytearray) else default
        )
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, default))


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, object]:
    """执行 处理 safe arguments 的内部辅助逻辑。

    Args:
        arguments: arguments 参数。
    """
    safe = dict(arguments)
    if "content" in safe:
        safe["content"] = "[redacted-memory-content]"
    return safe


_MEMORY_TOOL_DEFS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
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
