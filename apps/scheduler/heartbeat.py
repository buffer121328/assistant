from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant_api.config import Settings
from assistant_api.monitoring import run_phase09_monitoring
from packages.agent_harness.evolution import BehaviorEvolutionService
from packages.memory.maintenance import maintain_memories
from packages.notifications import ReminderService, deliver_langbot_due
from assistant_api.langbot import LangBotResultClient


DispatchTask = Callable[[str], Awaitable[None]]


async def run_v2_maintenance(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    dispatch_task: DispatchTask | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = await run_phase09_monitoring(
        sessionmaker=sessionmaker,
        settings=settings,
        dispatch_task=dispatch_task,
        now=now,
    )

    async with sessionmaker() as session:
        memory_result = await maintain_memories(session=session, now=now)
        suggestion = await BehaviorEvolutionService(session).evaluate(now=now)
        await session.commit()

    async with sessionmaker() as session:
        created_outbox_ids = await ReminderService(session).materialize_due(now=now)

    async with sessionmaker() as session:
        delivered_outbox_ids = await deliver_langbot_due(
            session=session,
            client=LangBotResultClient(settings),
            now=now,
        )

    result["archived_memory_ids"] = list(memory_result.archived_memory_ids)
    result["evolution_suggestion_created"] = suggestion is not None
    result["created_notification_outbox_ids"] = list(created_outbox_ids)
    result["delivered_notification_outbox_ids"] = list(delivered_outbox_ids)
    return result
