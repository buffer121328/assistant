from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from domain.models import (
    Base,
    Memory,
    MemoryFeedback,
    MemoryIndexOutbox,
    MemoryLink,
    User,
)
from application.memory_service import ForbiddenMemoryContentError, MemoryService
from memory.semantic import SemanticMemoryResult


class FailingSemanticMemory:
    enabled = True

    def __init__(self) -> None:
        self.adds: list[tuple[str, str, str, str]] = []

    async def add(
        self, *, user_id: str, run_id: str, memory_id: str, content: str
    ) -> bool:
        self.adds.append((user_id, run_id, memory_id, content))
        raise RuntimeError("synthetic semantic failure")

    async def delete(self, *, user_id: str, memory_id: str) -> bool:
        raise RuntimeError("synthetic semantic failure")

    async def search(
        self, *, user_id: str, query: str, limit: int
    ) -> tuple[SemanticMemoryResult, ...]:
        return ()


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v6-memory.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_user(
    sessionmaker: async_sessionmaker[AsyncSession], name: str
) -> User:
    async with sessionmaker() as session:
        user = User(display_name=name)
        session.add(user)
        await session.commit()
        return user


@pytest.mark.asyncio
async def test_explicit_memory_records_contract_and_stable_hash(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await create_user(sessionmaker, "owner")
    async with sessionmaker() as session:
        service = MemoryService(session)
        memory = await service.create_memory(
            user_id=user.id,
            content="  回答   保持简洁  ",
            source_kind="explicit_command",
            source_task_id=None,
        )
        await session.commit()

    assert memory.content == memory.normalized_content == "回答 保持简洁"
    assert len(memory.content_hash) == 64
    assert memory.status == "active"
    assert memory.scope_kind == "user/global"
    assert memory.sensitivity == "public"
    assert memory.confirmed_by_user is True
    assert memory.confirmed_at is not None
    assert memory.source_kind == "explicit_command"


@pytest.mark.asyncio
async def test_forbidden_memory_fails_before_sql_semantic_or_outbox(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await create_user(sessionmaker, "owner")
    semantic = FailingSemanticMemory()
    synthetic = "Authorization: Bearer synthetic-not-usable"
    async with sessionmaker() as session:
        service = MemoryService(session, semantic_memory=semantic)
        with pytest.raises(ForbiddenMemoryContentError) as exc_info:
            await service.create_memory(user_id=user.id, content=synthetic)
        assert synthetic not in str(exc_info.value)
        assert (await session.scalars(select(Memory))).all() == []
        assert (await session.scalars(select(MemoryIndexOutbox))).all() == []
    assert semantic.adds == []


@pytest.mark.asyncio
async def test_candidate_source_replay_is_idempotent_and_owner_scoped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner = await create_user(sessionmaker, "owner")
    other = await create_user(sessionmaker, "other")
    async with sessionmaker() as session:
        service = MemoryService(session)
        first = await service.create_memory(
            user_id=owner.id,
            content="候选偏好",
            source_kind="conversation_message",
            source_message_id="message-1",
            confirmed_by_user=False,
        )
        replay = await service.create_memory(
            user_id=owner.id,
            content="候选偏好",
            source_kind="conversation_message",
            source_message_id="message-1",
            confirmed_by_user=False,
        )
        foreign = await service.create_memory(
            user_id=other.id,
            content="候选偏好",
            source_kind="conversation_message",
            source_message_id="message-1",
            confirmed_by_user=False,
        )
        await session.commit()
    assert first.id == replay.id
    assert foreign.id != first.id
    assert first.status == "candidate"
    assert first.confirmed_by_user is False


@pytest.mark.asyncio
async def test_confirmed_correction_supersedes_without_overwrite(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner = await create_user(sessionmaker, "owner")
    other = await create_user(sessionmaker, "other")
    async with sessionmaker() as session:
        service = MemoryService(session)
        original = await service.create_memory(user_id=owner.id, content="喜欢浅色")
        corrected = await service.correct_memory(
            user_id=owner.id,
            memory_id=original.id,
            content="喜欢深色",
            confirm=True,
        )
        await service.add_feedback(
            user_id=owner.id,
            memory_id=corrected.id,
            feedback_type="confirmed",
        )
        with pytest.raises(Exception):
            await service.correct_memory(
                user_id=other.id,
                memory_id=original.id,
                content="越权修正",
                confirm=True,
            )
        await session.commit()
        links = (await session.scalars(select(MemoryLink))).all()
        feedback = (await session.scalars(select(MemoryFeedback))).all()

    assert original.content == "喜欢浅色"
    assert original.status == "superseded"
    assert corrected.content == "喜欢深色"
    assert corrected.status == "active"
    assert corrected.supersedes_id == original.id
    assert [
        (item.source_memory_id, item.target_memory_id, item.link_type) for item in links
    ] == [(corrected.id, original.id, "supersedes")]
    assert feedback[0].user_id == owner.id
    assert feedback[0].memory_id == corrected.id


def get_tables(session: Session) -> set[str]:
    return set(inspect(session.get_bind()).get_table_names())


@pytest.mark.asyncio
async def test_v6_memory_tables_are_in_database_backup_scope(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        tables = await session.run_sync(get_tables)
    assert {
        "memories",
        "memory_links",
        "memory_feedback",
        "memory_index_outbox",
    }.issubset(tables)


@pytest.mark.asyncio
async def test_candidate_confirm_reject_pin_scope_and_archive_are_owned(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner = await create_user(sessionmaker, "owner")
    async with sessionmaker() as session:
        service = MemoryService(session)
        confirmed = await service.create_memory(
            user_id=owner.id, content="项目偏好", confirmed_by_user=False
        )
        rejected = await service.create_memory(
            user_id=owner.id, content="临时候选", confirmed_by_user=False
        )
        await service.confirm_memory(user_id=owner.id, memory_id=confirmed.id)
        await service.set_memory_pinned(
            user_id=owner.id, memory_id=confirmed.id, pinned=True
        )
        await service.change_memory_scope(
            user_id=owner.id,
            memory_id=confirmed.id,
            scope_kind="user/project",
            scope_id="project-synthetic",
        )
        await service.reject_memory(user_id=owner.id, memory_id=rejected.id)
        await service.archive_memory(user_id=owner.id, memory_id=confirmed.id)
        await session.commit()

    assert confirmed.status == "archived"
    assert confirmed.confirmed_by_user is True
    assert confirmed.is_pinned is True
    assert confirmed.scope_kind == "user/project"
    assert confirmed.scope_id == "project-synthetic"
    assert rejected.status == "rejected"
    assert rejected.is_active is False


@pytest.mark.asyncio
async def test_enabled_semantic_failure_creates_one_pending_outbox(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner = await create_user(sessionmaker, "owner")
    async with sessionmaker() as session:
        from domain.models import Task, TaskStatus

        task = Task(
            user_id=owner.id,
            platform="langbot",
            task_type="memory",
            input_text="/memory 记住 回答先给结论",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.commit()
        semantic = FailingSemanticMemory()
        result = await MemoryService(session, semantic_memory=semantic).execute_task(
            task.id
        )
        outbox = (await session.scalars(select(MemoryIndexOutbox))).all()
        memories = (await session.scalars(select(Memory))).all()

    assert result.status == "success"
    assert len(memories) == len(outbox) == 1
    assert outbox[0].memory_id == memories[0].id
    assert outbox[0].operation == "add"
    assert outbox[0].status == "pending"
    assert outbox[0].last_error_code == "semantic_add_failed"


@pytest.mark.asyncio
async def test_disabled_semantic_memory_stays_sql_only(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner = await create_user(sessionmaker, "owner")
    async with sessionmaker() as session:
        from domain.models import Task, TaskStatus

        task = Task(
            user_id=owner.id,
            platform="langbot",
            task_type="memory",
            input_text="/memory 记住 SQL only",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.commit()
        result = await MemoryService(session).execute_task(task.id)
        outbox = (await session.scalars(select(MemoryIndexOutbox))).all()

    assert result.status == "success"
    assert outbox == []


def test_v6_migration_has_linear_upgrade_and_downgrade_contract() -> None:
    import importlib

    migration = importlib.import_module(
        "backend.migrations.versions.202607150004_v6_memory_contract_safety"
    )
    assert migration.revision == "202607150004"
    assert migration.down_revision == "202607150003"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)
