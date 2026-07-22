from __future__ import annotations

from collections.abc import AsyncIterator
import importlib
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from memory.index_outbox import MemoryIndexOutboxConsumer
from memory.semantic import NoopSemanticMemory
from domain.models import Base, Memory, MemoryIndexOutbox, User


class RecordingSemanticMemory:
    enabled = True

    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed
        self.adds: list[str] = []
        self.deletes: list[str] = []

    async def add(self, *, user_id: str, run_id: str, memory_id: str, content: str) -> bool:
        del user_id, run_id, content
        self.adds.append(memory_id)
        return self.succeed

    async def delete(self, *, user_id: str, memory_id: str) -> bool:
        del user_id
        self.deletes.append(memory_id)
        return self.succeed

    async def search(self, *, user_id: str, query: str, limit: int):  # type: ignore[no-untyped-def]
        del user_id, query, limit
        return ()


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/outbox.db", poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed(session: AsyncSession, operation: str) -> MemoryIndexOutbox:
    user = User(display_name="Outbox User")
    session.add(user)
    await session.flush()
    memory = Memory(user_id=user.id, content="remember this", status="active")
    session.add(memory)
    await session.flush()
    item = MemoryIndexOutbox(
        memory_id=memory.id,
        user_id=user.id,
        operation=operation,
        status="pending",
    )
    session.add(item)
    await session.commit()
    return item


@pytest.mark.asyncio
async def test_add_and_rebuild_operations_reach_succeeded(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    semantic = RecordingSemanticMemory()
    async with sessionmaker() as session:
        add_item = await seed(session, "add")
        add_result = await MemoryIndexOutboxConsumer(session, semantic_memory=semantic).run_once()
        stored_add = await session.get(MemoryIndexOutbox, add_item.id)

    assert add_result.succeeded_count == 1
    assert stored_add is not None and stored_add.status == "succeeded"
    assert semantic.adds == [add_item.memory_id]

    async with sessionmaker() as session:
        rebuild_item = await seed(session, "rebuild")
        rebuild_result = await MemoryIndexOutboxConsumer(session, semantic_memory=semantic).run_once()
        stored_rebuild = await session.get(MemoryIndexOutbox, rebuild_item.id)

    assert rebuild_result.succeeded_count == 1
    assert stored_rebuild is not None and stored_rebuild.status == "succeeded"
    assert rebuild_item.memory_id in semantic.deletes
    assert rebuild_item.memory_id in semantic.adds


@pytest.mark.asyncio
async def test_failures_retry_then_reach_failed(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    semantic = RecordingSemanticMemory(succeed=False)
    async with sessionmaker() as session:
        item = await seed(session, "add")
        consumer = MemoryIndexOutboxConsumer(session, semantic_memory=semantic, max_attempts=2)
        first = await consumer.run_once()
        second = await consumer.run_once()
        stored = await session.get(MemoryIndexOutbox, item.id)

    assert first.retry_count == 1
    assert second.failed_count == 1
    assert stored is not None
    assert stored.status == "failed"
    assert stored.attempts == 2
    assert stored.last_error_code == "semantic_add_failed"


@pytest.mark.asyncio
async def test_disabled_backend_finishes_as_failed(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with sessionmaker() as session:
        item = await seed(session, "delete")
        result = await MemoryIndexOutboxConsumer(
            session,
            semantic_memory=NoopSemanticMemory(),
        ).run_once()
        stored = await session.get(MemoryIndexOutbox, item.id)

    assert result.failed_count == 1
    assert stored is not None
    assert stored.status == "failed"
    assert stored.last_error_code == "semantic_memory_disabled"


def test_outbox_consumer_migration_is_linear() -> None:
    migration = importlib.import_module(
        "backend.migrations.versions.202607210003_v11_memory_index_outbox_consumer"
    )
    assert migration.revision == "202607210003"
    assert migration.down_revision == "202607210002"
    assert callable(migration.upgrade) and callable(migration.downgrade)
