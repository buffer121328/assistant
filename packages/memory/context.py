from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from .retrieval import RetrievalResult, retrieve_memories
from .semantic import SemanticMemory


async def load_memory_context(
    *,
    session: AsyncSession,
    user_id: str,
    now: datetime | None = None,
    query: str = "",
    semantic_memory: SemanticMemory | None = None,
    semantic_limit: int = 5,
    task_id: str | None = None,
    conversation_id: str | None = None,
    scope_kind: str = "user/global",
    scope_id: str | None = None,
) -> RetrievalResult:
    from assistant_api.memory_release import load_active_retrieval_weights

    weights = await load_active_retrieval_weights(
        session=session,
        user_id=user_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        max_items_limit=max(1, min(semantic_limit, 20)),
    )
    return await retrieve_memories(
        session=session,
        user_id=user_id,
        now=now,
        query=query,
        semantic_memory=semantic_memory,
        weights=weights,
        task_id=task_id,
        conversation_id=conversation_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
    )


async def load_memory_summary(
    *,
    session: AsyncSession,
    user_id: str,
    now: datetime | None = None,
    query: str = "",
    semantic_memory: SemanticMemory | None = None,
    semantic_limit: int = 5,
) -> str:
    result = await load_memory_context(
        session=session,
        user_id=user_id,
        now=now,
        query=query,
        semantic_memory=semantic_memory,
        semantic_limit=semantic_limit,
    )
    return "\n".join(item.content for item in result.items)
