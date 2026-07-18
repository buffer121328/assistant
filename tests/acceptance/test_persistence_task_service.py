from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.main import create_app
from domain.models import Base, Task, TaskStatus, User
from domain.services import TaskService


@pytest_asyncio.fixture
async def db_sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


async def create_user(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    display_name: str = "Test User",
) -> str:
    async with sessionmaker() as session:
        user = User(display_name=display_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    status: TaskStatus = TaskStatus.PENDING,
    input_text: str = "plan my week",
) -> Task:
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="api",
            task_type="plan",
            input_text=input_text,
            status=status.value,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


def create_test_client(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> TestClient:
    app = create_app()
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


def get_table_names(sync_session: Session) -> set[str]:
    return set(inspect(sync_session.get_bind()).get_table_names())


@pytest.mark.asyncio
async def test_mvp_tables_are_created(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        table_names = await session.run_sync(get_table_names)

    assert {
        "users",
        "platform_accounts",
        "tasks",
        "memories",
        "model_logs",
        "tool_logs",
        "approvals",
    }.issubset(table_names)


@pytest.mark.asyncio
async def test_create_task_api_creates_pending_task(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(db_sessionmaker)

    with create_test_client(db_sessionmaker) as client:
        response = client.post(
            "/api/tasks",
            json={
                "user_id": user_id,
                "platform": "api",
                "task_type": "plan",
                "input_text": "plan my week",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["task_id"]
    assert payload["status"] == "pending"

    async with db_sessionmaker() as session:
        task = await session.get(Task, payload["task_id"])

    assert task is not None
    assert task.user_id == user_id
    assert task.input_text == "plan my week"


@pytest.mark.asyncio
async def test_create_task_api_rejects_missing_user(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    with create_test_client(db_sessionmaker) as client:
        response = client.post(
            "/api/tasks",
            json={
                "user_id": "missing-user-id",
                "platform": "api",
                "task_type": "plan",
                "input_text": "plan my week",
            },
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"

    async with db_sessionmaker() as session:
        tasks = (await session.scalars(select(Task))).all()

    assert tasks == []


@pytest.mark.asyncio
async def test_get_task_detail_api_returns_task_fields(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(db_sessionmaker)
    task = await create_task(
        db_sessionmaker,
        user_id=user_id,
        status=TaskStatus.RUNNING,
        input_text="check status",
    )

    with create_test_client(db_sessionmaker) as client:
        response = client.get(f"/api/tasks/{task.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task.id
    assert payload["status"] == "running"
    assert payload["input_text"] == "check status"
    assert payload["task_type"] == "plan"
    assert payload["created_at"]
    assert payload["updated_at"]


@pytest.mark.asyncio
async def test_list_tasks_api_returns_one_users_tasks_in_reverse_creation_order(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    first_user_id = await create_user(db_sessionmaker, display_name="First User")
    second_user_id = await create_user(db_sessionmaker, display_name="Second User")
    older_task = await create_task(
        db_sessionmaker,
        user_id=first_user_id,
        input_text="older",
    )
    newer_task = await create_task(
        db_sessionmaker,
        user_id=first_user_id,
        input_text="newer",
    )
    await create_task(db_sessionmaker, user_id=second_user_id, input_text="other")

    with create_test_client(db_sessionmaker) as client:
        response = client.get("/api/tasks", params={"user_id": first_user_id})

    assert response.status_code == 200
    payload = response.json()
    assert [item["task_id"] for item in payload["items"]] == [
        newer_task.id,
        older_task.id,
    ]
    assert all(item["user_id"] == first_user_id for item in payload["items"])


@pytest.mark.asyncio
async def test_task_service_allows_valid_status_transition(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(db_sessionmaker)
    task = await create_task(db_sessionmaker, user_id=user_id)

    async with db_sessionmaker() as session:
        updated = await TaskService(session).update_status(
            task.id,
            TaskStatus.RUNNING,
        )

    assert updated.status == "running"
    assert updated.updated_at >= task.updated_at


@pytest.mark.asyncio
async def test_task_service_rejects_invalid_status_transition(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(db_sessionmaker)
    task = await create_task(
        db_sessionmaker,
        user_id=user_id,
        status=TaskStatus.SUCCESS,
    )

    async with db_sessionmaker() as session:
        with pytest.raises(ValueError, match="Invalid task status transition"):
            await TaskService(session).update_status(task.id, TaskStatus.RUNNING)

    async with db_sessionmaker() as session:
        unchanged = await session.get(Task, task.id)

    assert unchanged is not None
    assert unchanged.status == "success"


@pytest.mark.asyncio
async def test_task_service_saves_success_result(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(db_sessionmaker)
    task = await create_task(
        db_sessionmaker,
        user_id=user_id,
        status=TaskStatus.RUNNING,
    )

    async with db_sessionmaker() as session:
        updated = await TaskService(session).save_success(
            task.id,
            result_text="done",
        )

    assert updated.status == "success"
    assert updated.result_text == "done"
    assert updated.error_message is None


@pytest.mark.asyncio
async def test_task_service_saves_failure_error(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(db_sessionmaker)
    task = await create_task(
        db_sessionmaker,
        user_id=user_id,
        status=TaskStatus.RUNNING,
    )

    async with db_sessionmaker() as session:
        updated = await TaskService(session).save_failure(
            task.id,
            error_message="model failed",
        )

    assert updated.status == "failed"
    assert updated.error_message == "model failed"
