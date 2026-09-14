from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.main import create_app
from application.task_execution.lifecycle import TaskService, TaskServiceError
from domain.models import Base, Task, User, TaskEvent
from infrastructure.settings.config import Settings


def settings_without_env(**values: object) -> Settings:
    """Call Pydantic Settings' documented runtime constructor override safely."""
    factory = cast(Callable[..., Settings], Settings)
    return factory(_env_file=None, **values)


@pytest_asyncio.fixture
async def db_sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cancel.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    status: str,
    second_user: bool = False,
) -> tuple[str, str, str | None]:
    async with sessionmaker() as session:
        user = User(display_name="Cancel Owner")
        session.add(user)
        await session.flush()
        other_id: str | None = None
        if second_user:
            other = User(display_name="Cancel Stranger")
            session.add(other)
            await session.flush()
            other_id = other.id
        task = Task(
            user_id=user.id,
            platform="local",
            task_type="plan",
            input_text="to be cancelled",
            status=status,
        )
        session.add(task)
        await session.commit()
        return user.id, task.id, other_id


def _cancel(client: TestClient, task_id: str, user_id: str):
    return client.post(
        f"/local/tasks/{task_id}/cancel",
        params={"user_id": user_id},
    )


@pytest.mark.asyncio
async def test_owner_can_cancel_pending_task(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id, task_id, _ = await _create_task(db_sessionmaker, status="pending")
    app = create_app(settings_without_env(redis_url="redis://placeholder"))
    app.state.db_sessionmaker = db_sessionmaker

    with TestClient(app) as client:
        response = _cancel(client, task_id, user_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["result_text"] == "任务已被用户取消。"

    async with db_sessionmaker() as session:
        result = await session.execute(
            select(TaskEvent)
            .where(
                TaskEvent.task_id == task_id,
                TaskEvent.event_type == "task.status.changed",
            )
            .order_by(TaskEvent.sequence)
        )
        events = result.scalars().all()
    assert events, "cancel must append a task.status.changed event"
    assert '"status": "cancelled"' in events[-1].payload_json


@pytest.mark.asyncio
async def test_owner_can_cancel_running_and_waiting_approval_tasks(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(settings_without_env(redis_url="redis://placeholder"))
    app.state.db_sessionmaker = db_sessionmaker

    for status in ("running", "waiting_approval"):
        user_id, task_id, _ = await _create_task(db_sessionmaker, status=status)
        with TestClient(app) as client:
            response = _cancel(client, task_id, user_id)
        assert response.status_code == 200, status
        assert response.json()["status"] == "cancelled", status


@pytest.mark.asyncio
async def test_non_owner_cancel_returns_404_and_keeps_task(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id, task_id, other_id = await _create_task(
        db_sessionmaker, status="running", second_user=True
    )
    app = create_app(settings_without_env(redis_url="redis://placeholder"))
    app.state.db_sessionmaker = db_sessionmaker

    with TestClient(app) as client:
        response = _cancel(client, task_id, cast(str, other_id))

    assert response.status_code == 404
    async with db_sessionmaker() as session:
        task = await session.get(Task, task_id)
    assert task is not None
    assert task.status == "running"


@pytest.mark.asyncio
async def test_terminal_task_cancel_returns_409(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(settings_without_env(redis_url="redis://placeholder"))
    app.state.db_sessionmaker = db_sessionmaker

    for status in ("success", "failed", "cancelled"):
        user_id, task_id, _ = await _create_task(db_sessionmaker, status=status)
        with TestClient(app) as client:
            response = _cancel(client, task_id, user_id)
        assert response.status_code == 409, status
        assert response.json()["error"]["code"] == "invalid_task_status_transition"


@pytest.mark.asyncio
async def test_cancelled_task_survives_late_worker_success_write(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A worker holding a stale in-memory copy cannot overwrite a user cancel."""
    user_id, task_id, _ = await _create_task(db_sessionmaker, status="running")
    app = create_app(settings_without_env(redis_url="redis://placeholder"))
    app.state.db_sessionmaker = db_sessionmaker

    async with db_sessionmaker() as worker_session:
        stale = await worker_session.get(Task, task_id)
        assert stale is not None and stale.status == "running"
        await worker_session.commit()

        with TestClient(app) as client:
            response = _cancel(client, task_id, user_id)
        assert response.status_code == 200

        with pytest.raises(TaskServiceError):
            await TaskService(worker_session).save_success(task_id, "late result")
        await worker_session.rollback()

    async with db_sessionmaker() as session:
        task = await session.get(Task, task_id)
    assert task is not None
    assert task.status == "cancelled"
