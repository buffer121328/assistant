from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from infrastructure.config import Settings
from app.main import create_app
from domain.models import Base, Task, TaskStatus, User
from workers.runtime import execute_task_by_id


ROOT = Path(__file__).parents[2]
RETIRED_PROVIDER_MARKERS = ("dify", "feishu")


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v3-04.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_user(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
    async with sessionmaker() as session:
        user = User(display_name="V3-04 Owner")
        session.add(user)
        await session.commit()
        return user.id


def test_current_runtime_and_configuration_have_no_retired_integrations() -> None:
    settings_fields = Settings.model_fields
    assert not any(
        marker in field.casefold()
        for field in settings_fields
        for marker in RETIRED_PROVIDER_MARKERS
    )

    for path in (ROOT / "apps", ROOT / "packages"):
        for source_file in path.rglob("*.py"):
            source = source_file.read_text(encoding="utf-8").casefold()
            assert not any(marker in source for marker in RETIRED_PROVIDER_MARKERS), (
                source_file
            )

    for filename in (".env.example", "docker-compose.yml"):
        content = (ROOT / filename).read_text(encoding="utf-8").casefold()
        assert not any(marker in content for marker in RETIRED_PROVIDER_MARKERS)

    route_paths = set(create_app().openapi()["paths"])
    assert "/api/webhooks/langbot" in route_paths
    assert not any(
        marker in route.casefold()
        for route in route_paths
        for marker in RETIRED_PROVIDER_MARKERS
    )


@pytest.mark.asyncio
async def test_task_api_accepts_only_current_model_classes(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(sessionmaker)
    app = create_app(Settings(database_url="sqlite+aiosqlite:///unused.db"))
    app.state.db_sessionmaker = sessionmaker
    base_payload = {
        "user_id": user_id,
        "platform": "desktop",
        "task_type": "plan",
        "input_text": "Create a safe plan.",
    }

    with TestClient(app) as client:
        accepted = [
            client.post("/api/tasks", json=base_payload),
            client.post("/api/tasks", json={**base_payload, "model_class": "light"}),
            client.post(
                "/api/tasks",
                json={**base_payload, "model_class": "standard"},
            ),
        ]
        rejected = [
            client.post(
                "/api/tasks",
                json={**base_payload, "model_class": "legacy_dify"},
            ),
            client.post(
                "/api/tasks",
                json={**base_payload, "model_class": "unknown"},
            ),
        ]

    assert [response.status_code for response in accepted] == [201, 201, 201]
    assert [response.status_code for response in rejected] == [422, 422]
    async with sessionmaker() as session:
        task_count = await session.scalar(select(func.count(Task.id)))
    assert task_count == 3


class FailingIfCalledExecutor:
    def __init__(self) -> None:
        self.called = False

    async def execute(self, **kwargs: Any) -> None:
        del kwargs
        self.called = True
        raise AssertionError("Executor must not run for an unsupported model class")


@pytest.mark.asyncio
async def test_worker_fails_historical_unknown_model_class_without_execution(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(sessionmaker)
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="desktop",
            task_type="plan",
            input_text="Historical task",
            status=TaskStatus.PENDING.value,
            model_class="retired-workflow",
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    executor = FailingIfCalledExecutor()
    result = await execute_task_by_id(
        task_id,
        sessionmaker=sessionmaker,
        settings=Settings(database_url="sqlite+aiosqlite:///unused.db"),
        langgraph_executor=executor,
    )

    assert result.status == TaskStatus.FAILED.value
    assert result.model_class == "retired-workflow"
    assert result.error_message == "Unsupported model class"
    assert executor.called is False
