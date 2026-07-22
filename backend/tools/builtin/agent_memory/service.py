from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from memory.user_memory import (
    ForbiddenMemoryContentError,
    MemoryNotFoundError,
    MemoryService,
)
from domain.models import ToolLog
from domain.policies.redaction import sanitize_text
from memory.agentic import classify_memory_query_type, rrf_weights_for_query
from memory.retrieval import RetrievalWeights, retrieve_memories
from memory.safety import classify_memory_sensitivity
from memory.semantic import SemanticMemory
from tools.builtin.agent_memory.utils import positive_int, safe_arguments
from tools.core.registry import ToolInvocation


@dataclass
class AgentMemoryToolService:
    """Backend implementation for built-in agent memory tools."""

    session: AsyncSession
    semantic_memory: SemanticMemory | None = None
    max_items: int = 5
    token_budget: int = 400

    async def remember(self, invocation: ToolInvocation) -> dict[str, object]:
        """Store explicit or candidate memory through MemoryService."""
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
        """Recall bounded owner-scoped memories with trace metadata."""
        query = str(invocation.arguments.get("query") or "")
        query_type = classify_memory_query_type(query)
        max_items = positive_int(invocation.arguments.get("max_items"), self.max_items)
        token_budget = positive_int(
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
        """Archive an owned memory by default."""
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
        """Persist a sanitized audit log entry for memory tool invocations."""
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
                        "arguments": safe_arguments(invocation.arguments),
                    }
                ),
                output_text=sanitize_text(output) if output is not None else None,
                error_message=sanitize_text(error) if error else None,
            )
        )
        await self.session.flush()
