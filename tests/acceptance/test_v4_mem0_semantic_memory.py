from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from assistant_api.models import Base, Memory, Task, User
from assistant_api.services import MemoryService
from packages.memory import SemanticMemoryResult, load_memory_summary


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v4-memory.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class FakeSemanticMemory:
    enabled = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.adds: list[dict[str, str]] = []
        self.deletes: list[tuple[str, str]] = []
        self.searches: list[tuple[str, str, int]] = []

    async def add(self, *, user_id: str, run_id: str, memory_id: str, content: str) -> bool:
        if self.fail:
            return False
        self.adds.append({"user_id": user_id, "run_id": run_id, "memory_id": memory_id, "content": content})
        return True

    async def delete(self, *, user_id: str, memory_id: str) -> bool:
        self.deletes.append((user_id, memory_id))
        return not self.fail

    async def search(self, *, user_id: str, query: str, limit: int) -> tuple[SemanticMemoryResult, ...]:
        self.searches.append((user_id, query, limit))
        if self.fail:
            raise RuntimeError("semantic unavailable")
        return (
            SemanticMemoryResult(memory_id="semantic-1", content="相关项目偏好", score=0.9),
            SemanticMemoryResult(memory_id="semantic-2", content="先给结论", score=0.8),
        )


@pytest.mark.asyncio
async def test_memory_command_keeps_sql_and_syncs_mem0_with_exact_ids(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    semantic = FakeSemanticMemory()
    async with sessionmaker() as session:
        user = User(display_name="memory")
        session.add(user)
        await session.flush()
        task = Task(user_id=user.id, platform="api", task_type="memory", input_text="/memory 记住 先给结论", status="pending")
        session.add(task)
        await session.commit()
        await MemoryService(session, semantic_memory=semantic).execute_task(task.id)
        stored = await session.scalar(select(Memory))
        assert stored is not None

    assert semantic.adds == [{"user_id": user.id, "run_id": task.id, "memory_id": stored.id, "content": "先给结论"}]
    assert "语义记忆已同步" in (task.result_text or "")


@pytest.mark.asyncio
async def test_semantic_context_is_query_relevant_and_sql_fallback_is_stable(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    semantic = FakeSemanticMemory()
    async with sessionmaker() as session:
        user = User(display_name="memory")
        other = User(display_name="other")
        session.add_all((user, other))
        await session.flush()
        session.add_all(
            (
                Memory(user_id=user.id, content="先给结论", memory_type="preference"),
                Memory(user_id=other.id, content="其他用户私密内容", memory_type="preference"),
            )
        )
        await session.commit()
        summary = await load_memory_summary(
            session=session,
            user_id=user.id,
            query="项目怎么安排",
            semantic_memory=semantic,
            semantic_limit=3,
        )

    assert semantic.searches == [(user.id, "项目怎么安排", 3)]
    assert summary.splitlines() == ["相关项目偏好", "先给结论"]
    assert "其他用户" not in summary

    failing = FakeSemanticMemory(fail=True)
    async with sessionmaker() as session:
        fallback = await load_memory_summary(
            session=session,
            user_id=user.id,
            query="项目怎么安排",
            semantic_memory=failing,
        )
    assert fallback == "先给结论"
