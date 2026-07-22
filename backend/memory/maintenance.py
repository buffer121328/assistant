from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import Memory, ToolLog, utc_now


MEMORY_MAINTENANCE_TOOL_NAME = "memory.maintenance"


@dataclass(frozen=True)
class MemoryMaintenanceResult:
    """表示 处理 memory maintenance result 的后端数据结构或服务对象。"""

    archived_memory_ids: tuple[str, ...]


async def maintain_memories(
    *,
    session: AsyncSession,
    now: datetime | None = None,
    stale_after_days: int = 30,
    max_stale_importance: int = 1,
    max_stale_access_count: int = 0,
) -> MemoryMaintenanceResult:
    """处理 maintain memories。

    Args:
        session: session 参数。
        now: now 参数。
        stale_after_days: stale_after_days 参数。
        max_stale_importance: max_stale_importance 参数。
        max_stale_access_count: max_stale_access_count 参数。
    """
    evaluated_at = now or utc_now()
    stale_before = evaluated_at - timedelta(days=stale_after_days)
    memories = list(
        await session.scalars(
            select(Memory)
            .where(
                Memory.is_active.is_(True),
                Memory.deleted_at.is_(None),
                Memory.archived_at.is_(None),
                or_(
                    and_(
                        Memory.expires_at.is_not(None),
                        Memory.expires_at <= evaluated_at,
                    ),
                    and_(
                        Memory.memory_type == "working",
                        Memory.updated_at <= stale_before,
                        Memory.importance_score <= max_stale_importance,
                        Memory.access_count <= max_stale_access_count,
                    ),
                ),
            )
            .order_by(Memory.created_at.asc(), Memory.id.asc())
        )
    )
    for memory in memories:
        memory.is_active = False
        memory.archived_at = evaluated_at

    archived_ids = tuple(memory.id for memory in memories)
    session.add(
        ToolLog(
            tool_name=MEMORY_MAINTENANCE_TOOL_NAME,
            status="succeeded",
            output_text=json.dumps(
                {"archived_count": len(archived_ids)},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
    )
    await session.flush()
    return MemoryMaintenanceResult(archived_memory_ids=archived_ids)
