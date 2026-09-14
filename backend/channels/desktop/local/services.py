from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.tasks import _enqueue_task_execution
from app.support.errors import AppError
from domain.models import Task, TaskEvent
from workers.worker import safe_revoke_task_execution as _revoke_task_execution

LOGGER = logging.getLogger("assistant_api")


async def get_owned_task(
    session: AsyncSession, *, task_id: str, user_id: str
) -> Task:
    """Load a task owned by the local user or raise a public 404.

    Args:
        session: 当前数据库异步会话。
        task_id: 目标任务 ID。
        user_id: 目标用户 ID。
    """
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
    """Resolve a cursor event id into its sequence number.

    Args:
        session: 当前数据库异步会话。
        task_id: 目标任务 ID。
        after_event_id: 用于执行当前操作的 after event id 参数。
    """
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


def safe_enqueue_task_execution(
    task_id: str,
    *,
    runtime_settings: object,
    tenant_id: str | None = None,
    organization_ids: tuple[str, ...] = (),
) -> bool:
    """Best-effort local task enqueue wrapper used by desktop routes.

    Args:
        task_id: 目标任务 ID。
        runtime_settings: 用于执行当前操作的 runtime settings 参数。
        tenant_id: 用于执行当前操作的 tenant id 参数。
        organization_ids: 用于执行当前操作的 organization ids 参数。
    """
    try:
        return _enqueue_task_execution(
            task_id,
            runtime_settings=runtime_settings,
            tenant_id=tenant_id,
            organization_ids=organization_ids,
        )
    except Exception:
        LOGGER.warning("local_task_enqueue_failed", exc_info=True)
        return False


def revoke_queued_task_execution(task_id: str) -> bool:
    """Best-effort local queue revoke; the task ledger stays authoritative.

    Args:
        task_id: 目标任务 ID。
    """
    try:
        return _revoke_task_execution(task_id)
    except Exception:
        LOGGER.warning("local_task_revoke_failed", task_id=task_id, exc_info=True)
        return False
