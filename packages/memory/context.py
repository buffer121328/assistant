from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import utc_now
from assistant_api.repositories import MemoryRepository


async def load_memory_summary(
    *,
    session: AsyncSession,
    user_id: str,
    now: datetime | None = None,
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
    return "\n".join(memory.content for memory in memories)
