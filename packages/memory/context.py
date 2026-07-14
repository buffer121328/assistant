from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import utc_now
from assistant_api.repositories import MemoryRepository
from .semantic import SemanticMemory


async def load_memory_summary(
    *,
    session: AsyncSession,
    user_id: str,
    now: datetime | None = None,
    query: str = "",
    semantic_memory: SemanticMemory | None = None,
    semantic_limit: int = 5,
) -> str:
    accessed_at = now or utc_now()
    memories = await MemoryRepository(session).list_active_memories(
        user_id,
        now=accessed_at,
    )
    for memory in memories:
        memory.access_count += 1
        memory.last_accessed_at = accessed_at
    await session.flush()
    contents: list[str] = []
    if semantic_memory is not None and semantic_memory.enabled and query.strip():
        try:
            semantic_results = await semantic_memory.search(
                user_id=user_id,
                query=query.strip(),
                limit=max(1, min(semantic_limit, 20)),
            )
        except Exception:
            semantic_results = ()
        contents.extend(item.content for item in semantic_results)
    contents.extend(
        memory.content for memory in memories if memory.memory_type == "preference"
    )
    return "\n".join(dict.fromkeys(item for item in contents if item.strip()))
