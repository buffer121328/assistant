from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from model_gateway import sanitize_text

from domain.models import TaskEvent


LOGGER = logging.getLogger("assistant_api")
TASK_EVENT_STATUS = "status"
TASK_EVENT_CONTENT_DELTA = "content_delta"
TASK_EVENT_PLAN = "plan"
TASK_EVENT_APPEND_ATTEMPTS = 3


class TaskEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self, *, task_id: str, user_id: str, event_type: str, payload: dict[str, object]
    ) -> TaskEvent:
        safe = json.dumps(
            _safe_json_value(payload),
            ensure_ascii=False,
            default=str,
        )[:16000]
        last_error: IntegrityError | None = None
        for _ in range(TASK_EVENT_APPEND_ATTEMPTS):
            sequence = int(
                await self.session.scalar(
                    select(func.coalesce(func.max(TaskEvent.sequence), 0)).where(
                        TaskEvent.task_id == task_id
                    )
                )
                or 0
            ) + 1
            item = TaskEvent(
                task_id=task_id,
                user_id=user_id,
                event_type=event_type,
                sequence=sequence,
                payload_json=safe,
            )
            self.session.add(item)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                last_error = exc
                await self.session.rollback()
                continue
            return item
        assert last_error is not None
        raise last_error

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
            LOGGER.warning("task_event_publish_failed", exc_info=True)
            return

    async def publish_text(
        self, *, task_id: str, user_id: str, text: str, chunk_size: int = 160
    ) -> None:
        for start in range(0, len(text), chunk_size):
            await self.publish(
                task_id=task_id,
                user_id=user_id,
                event_type=TASK_EVENT_CONTENT_DELTA,
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


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {
            str(key): _safe_json_value(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [_safe_json_value(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return sanitize_text(value)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    return any(
        marker in normalized
        for marker in ("authorization", "cookie", "api_key", "apikey", "token", "secret")
    )
