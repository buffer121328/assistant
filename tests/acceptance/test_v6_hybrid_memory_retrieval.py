from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domain.models import (
    Base,
    MemoryRetrievalTrace,
    MemoryRetrievalTraceItem,
    User,
)
from domain.services import MemoryService
from memory.retrieval import RetrievalWeights, retrieve_memories
from memory.semantic import SemanticMemoryResult


class FakeSemantic:
    enabled = True

    def __init__(
        self, results: tuple[SemanticMemoryResult, ...] = (), *, fail: bool = False
    ) -> None:
        self.results = results
        self.fail = fail

    async def add(self, **kwargs: object) -> bool:
        return True

    async def delete(self, **kwargs: object) -> bool:
        return True

    async def search(self, **kwargs: object) -> tuple[SemanticMemoryResult, ...]:
        if self.fail:
            raise RuntimeError("synthetic semantic failure")
        return self.results


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/retrieval.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def user(sessionmaker: async_sessionmaker[AsyncSession], name: str) -> User:
    async with sessionmaker() as session:
        item = User(display_name=name)
        session.add(item)
        await session.commit()
        return item


@pytest.mark.asyncio
async def test_retrieval_injects_relevant_owned_memory_not_all_preferences(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner = await user(sessionmaker, "owner")
    other = await user(sessionmaker, "other")
    async with sessionmaker() as session:
        relevant = await MemoryService(session).create_memory(
            user_id=owner.id, content="回答喜欢先给结论"
        )
        await MemoryService(session).create_memory(
            user_id=owner.id, content="周末提醒买菜"
        )
        foreign = await MemoryService(session).create_memory(
            user_id=other.id, content="回答喜欢泄漏内容"
        )
        result = await retrieve_memories(
            session=session,
            user_id=owner.id,
            query="回答喜欢什么格式",
            semantic_memory=FakeSemantic(
                (
                    SemanticMemoryResult(relevant.id, "ignored", 0.9),
                    SemanticMemoryResult(foreign.id, "foreign", 1.0),
                )
            ),
        )
        traces = (await session.scalars(select(MemoryRetrievalTrace))).all()
        items = (await session.scalars(select(MemoryRetrievalTraceItem))).all()

    assert [item.memory_id for item in result.items] == [relevant.id]
    assert foreign.id not in {item.memory_id for item in items}
    assert traces[0].retrieval_mode == "hybrid"
    assert traces[0].query_hash != "回答喜欢什么格式"
    assert all("回答喜欢" not in item.component_scores_json for item in items)


@pytest.mark.asyncio
async def test_current_excludes_superseded_but_historical_can_return_it(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner = await user(sessionmaker, "owner")
    now = datetime(2026, 7, 16, tzinfo=UTC)
    async with sessionmaker() as session:
        old = await MemoryService(session).create_memory(
            user_id=owner.id, content="喜欢浅色"
        )
        new = await MemoryService(session).correct_memory(
            user_id=owner.id, memory_id=old.id, content="喜欢深色", confirm=True
        )
        current = await retrieve_memories(
            session=session, user_id=owner.id, query="当前喜欢什么颜色", now=now
        )
        historical = await retrieve_memories(
            session=session, user_id=owner.id, query="之前喜欢什么颜色", now=now
        )

    assert [item.memory_id for item in current.items] == [new.id]
    assert old.id in {item.memory_id for item in historical.items}
    assert all(item.historical for item in historical.items)


@pytest.mark.asyncio
async def test_semantic_failure_falls_back_and_token_budget_is_bounded(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner = await user(sessionmaker, "owner")
    async with sessionmaker() as session:
        await MemoryService(session).create_memory(
            user_id=owner.id, content="项目 使用 Python Python Python"
        )
        await MemoryService(session).create_memory(
            user_id=owner.id, content="项目 使用 PostgreSQL PostgreSQL"
        )
        result = await retrieve_memories(
            session=session,
            user_id=owner.id,
            query="项目使用什么",
            semantic_memory=FakeSemantic(fail=True),
            weights=RetrievalWeights(token_budget=6, max_items=2),
        )

    assert result.mode == "keyword_fallback"
    assert result.injected_tokens <= 6
    assert len(result.items) <= 1


def test_retrieval_migration_and_backup_contract() -> None:
    import importlib
    from scripts.ops.db_common import COUNTED_TABLES

    migration = importlib.import_module(
        "backend.migrations.versions.202607160002_v6_hybrid_memory_retrieval"
    )
    assert migration.revision == "202607160002"
    assert migration.down_revision == "202607160001"
    assert {"memory_retrieval_traces", "memory_retrieval_trace_items"}.issubset(
        COUNTED_TABLES
    )


@pytest.mark.asyncio
async def test_retrieval_api_is_owner_scoped_and_returns_safe_metadata(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from fastapi.testclient import TestClient
    from infrastructure.config import Settings
    from app.main import create_app
    from domain.models import Task, TaskStatus

    owner = await user(sessionmaker, "owner")
    other = await user(sessionmaker, "other")
    async with sessionmaker() as session:
        task = Task(
            user_id=owner.id,
            platform="api",
            task_type="plan",
            input_text="回答偏好",
            status=TaskStatus.RUNNING.value,
        )
        session.add(task)
        await session.flush()
        await MemoryService(session).create_memory(
            user_id=owner.id, content="回答先给结论"
        )
        await retrieve_memories(
            session=session, user_id=owner.id, query="回答偏好", task_id=task.id
        )
        await session.commit()
        task_id = task.id

    app = create_app(Settings(database_url="sqlite+aiosqlite:///unused.db"))
    app.state.db_sessionmaker = sessionmaker
    with TestClient(app) as client:
        allowed = client.get(
            f"/api/tasks/{task_id}/memory-retrieval", params={"user_id": owner.id}
        )
        denied = client.get(
            f"/api/tasks/{task_id}/memory-retrieval", params={"user_id": other.id}
        )

    assert allowed.status_code == 200
    assert allowed.json()["trace"]["injected_count"] == 1
    assert "content" not in repr(allowed.json())
    assert denied.status_code == 404
