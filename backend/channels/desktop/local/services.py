from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.tasks import _enqueue_task_execution
from app.support.errors import AppError
from domain.models import Task, TaskEvent

LOGGER = logging.getLogger("assistant_api")


async def get_owned_task(
    session: AsyncSession, *, task_id: str, user_id: str
) -> Task:
    """Load a task owned by the local user or raise a public 404."""
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    )
    if task is None:
        raise AppError("task_not_found", "Task not found.", 404)
    return task


async def sequence_after_event_id(
    session: AsyncSession,
    *,
    task_id: str,
    after_event_id: str | None,
) -> int:
    """Resolve a cursor event id into its sequence number."""
    if after_event_id is None:
        return 0
    event = await session.scalar(
        select(TaskEvent).where(
            TaskEvent.task_id == task_id, TaskEvent.id == after_event_id
        )
    )
    if event is None:
        raise AppError("event_cursor_not_found", "Event cursor not found.", 404)
    return event.sequence


def safe_enqueue_task_execution(task_id: str, *, runtime_settings: object) -> bool:
    """Best-effort local task enqueue wrapper used by desktop routes."""
    try:
        return _enqueue_task_execution(task_id, runtime_settings=runtime_settings)
    except Exception:
        LOGGER.warning("local_task_enqueue_failed", exc_info=True)
        return False
