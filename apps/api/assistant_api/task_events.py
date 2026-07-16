from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.model_gateway import sanitize_text

from .models import TaskEvent


class TaskEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self, *, task_id: str, user_id: str, event_type: str, payload: dict[str, object]
    ) -> TaskEvent:
        sequence = int(
            await self.session.scalar(
                select(func.coalesce(func.max(TaskEvent.sequence), 0)).where(
                    TaskEvent.task_id == task_id
                )
            )
            or 0
        ) + 1
        safe = sanitize_text(json.dumps(payload, ensure_ascii=False, default=str))[:16000]
        item = TaskEvent(
            task_id=task_id,
            user_id=user_id,
            event_type=event_type,
            sequence=sequence,
            payload_json=safe,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_after(self, *, task_id: str, after: int) -> list[TaskEvent]:
        items = await self.session.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.sequence > after)
            .order_by(TaskEvent.sequence.asc())
        )
        return list(items)


class TaskEventPublisher:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def publish(
        self, *, task_id: str, user_id: str, event_type: str, payload: dict[str, object]
    ) -> None:
        try:
            async with self.sessionmaker() as session:
                await TaskEventRepository(session).append(
                    task_id=task_id,
                    user_id=user_id,
                    event_type=event_type,
                    payload=payload,
                )
                await session.commit()
        except Exception:
            return

    async def publish_text(
        self, *, task_id: str, user_id: str, text: str, chunk_size: int = 160
    ) -> None:
        for start in range(0, len(text), chunk_size):
            await self.publish(
                task_id=task_id,
                user_id=user_id,
                event_type="content_delta",
                payload={"text": text[start : start + chunk_size]},
            )


def event_record(item: TaskEvent) -> dict[str, object]:
    payload = json.loads(item.payload_json)
    return {
        "sequence": item.sequence,
        "type": item.event_type,
        "payload": payload,
        "created_at": item.created_at.isoformat(),
    }
