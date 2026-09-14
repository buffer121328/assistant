from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import create_app
from application.session_context.spaces import SpaceService
from domain.models import Base, Task, TaskStatus, User
from infrastructure.settings.config import Settings
from workers import worker


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/desktop-session-resilience.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_user(sessionmaker: async_sessionmaker[AsyncSession]) -> User:
    async with sessionmaker() as session:
        user = User(display_name="Session resilience owner")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def test_task_publisher_uses_one_attempt_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker delivery must not invoke Celery's default retry policy on request paths."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class FakeTask:
        def apply_async(self, args: tuple[Any, ...], **kwargs: Any) -> None:
            calls.append((args, kwargs))

    monkeypatch.setattr(worker, "execute_task", FakeTask())
    for key in ("broker_url", "result_backend", "broker_connection_timeout", "broker_transport_options"):
        monkeypatch.setitem(worker.celery_app.conf, key, worker.celery_app.conf[key])

    queued = worker.enqueue_task_execution(
        "task-1",
        runtime_settings=Settings(redis_url="redis://queue.example.invalid:6379/0"),
        tenant_id="tenant-1",
        organization_ids=("org-1",),
    )

    assert queued is True
    assert calls == [(("task-1", "tenant-1", ["org-1"]), {"retry": False})]
    assert worker.celery_app.conf.broker_connection_timeout == 1.0
    assert worker.celery_app.conf.broker_transport_options == {
        "socket_connect_timeout": 1.0,
        "socket_timeout": 1.0,
    }


def test_local_submission_survives_queue_delivery_failure(
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server persists the first conversation task even when the broker rejects delivery."""
    owner = anyio.run(create_user, sessionmaker)

    async def create_workspace() -> str:
        async with sessionmaker() as session:
            item = await SpaceService(session).create(
                user_id=owner.id,
                name="会话工作区",
            )
            return item.id

    workspace_id = anyio.run(create_workspace)

    def fail_enqueue(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr("channels.desktop.local.services._enqueue_task_execution", fail_enqueue)
    app = create_app(Settings(redis_url="redis://placeholder"))
    app.state.db_sessionmaker = sessionmaker

    with TestClient(app) as client:
        response = client.post(
            "/local/tasks",
            json={
                "user_id": owner.id,
                "task_type": "office",
                "command_id": "office",
                "input_text": "创建一个不会被队列故障卡住的会话",
                "space_id": workspace_id,
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["queued"] is False
    assert body["task"]["conversation_id"]
    assert body["task"]["status"] == TaskStatus.PENDING.value

    async def load_task() -> Task | None:
        async with sessionmaker() as session:
            return await session.scalar(select(Task).where(Task.id == body["task"]["task_id"]))

    persisted = anyio.run(load_task)
    assert persisted is not None
    assert persisted.status == TaskStatus.PENDING.value
