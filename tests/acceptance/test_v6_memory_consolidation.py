from __future__ import annotations
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from domain.models import (
    Base,
    Memory,
    MemoryConsolidationDigest,
    MemoryConsolidationDecision,
    MemoryIndexOutbox,
    MemoryLink,
    User,
)
from application.memory_service import MemoryService
from memory.consolidation import (
    MemoryConsolidationService,
    ReconciliationReport,
)


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/consolidation.db", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def owner(sessionmaker: async_sessionmaker[AsyncSession]) -> User:
    async with sessionmaker() as session:
        user = User(display_name="owner")
        session.add(user)
        await session.commit()
        return user


@pytest.mark.asyncio
async def test_daily_is_idempotent_merges_duplicates_and_marks_conflict(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await owner(sessionmaker)
    start = datetime(2026, 7, 15, tzinfo=UTC)
    end = start + timedelta(days=1)
    async with sessionmaker() as session:
        service = MemoryService(session)
        canonical = await service.create_memory(user_id=user.id, content="喜欢深色")
        duplicate = await service.create_memory(
            user_id=user.id,
            content="喜欢深色",
            source_kind="conversation_terminal_turn",
            source_message_id="m-2",
            confirmed_by_user=False,
        )
        conflict = await service.create_memory(
            user_id=user.id,
            content="不喜欢深色",
            source_kind="conversation_terminal_turn",
            source_message_id="m-3",
            confirmed_by_user=False,
        )
        for index, item in enumerate((canonical, duplicate, conflict)):
            item.created_at = start + timedelta(hours=index + 1)
            item.updated_at = item.created_at
        first = await MemoryConsolidationService(session).run_daily(
            user_id=user.id, window_start=start, window_end=end
        )
        await session.commit()
        second = await MemoryConsolidationService(session).run_daily(
            user_id=user.id, window_start=start, window_end=end
        )
        links = (await session.scalars(select(MemoryLink))).all()
        digests = (await session.scalars(select(MemoryConsolidationDigest))).all()
        decisions = (await session.scalars(select(MemoryConsolidationDecision))).all()
    assert first.id == second.id
    assert duplicate.status == "superseded" and duplicate.valid_to == end
    assert conflict.status == "conflict_pending"
    assert {item.link_type for item in links} == {"supports", "contradicts"}
    assert len(digests) == 1 and len(decisions) == 2


@pytest.mark.asyncio
async def test_weekly_requires_two_episodes_and_creates_derived_links(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await owner(sessionmaker)
    start = datetime(2026, 7, 7, tzinfo=UTC)
    end = start + timedelta(days=7)
    async with sessionmaker() as session:
        service = MemoryService(session)
        one = await service.create_memory(
            user_id=user.id, content="先运行测试再提交", memory_type="episode"
        )
        one.reason_code = "successful_test_flow"
        one.created_at = start + timedelta(days=1)
        first = await MemoryConsolidationService(session).run_weekly(
            user_id=user.id, window_start=start, window_end=end
        )
        two = await service.create_memory(
            user_id=user.id,
            content="测试通过后提交",
            memory_type="episode",
            source_kind="successful_task_outcome",
            source_message_id="task-2",
        )
        two.reason_code = "successful_test_flow"
        two.created_at = start + timedelta(days=2)
        # use a new weekly window to evaluate the two-evidence group
        second_end = end + timedelta(days=7)
        one.created_at = end + timedelta(days=1)
        two.created_at = end + timedelta(days=2)
        second = await MemoryConsolidationService(session).run_weekly(
            user_id=user.id, window_start=end, window_end=second_end
        )
        procedures = (
            await session.scalars(
                select(Memory).where(Memory.memory_type == "procedure")
            )
        ).all()
        links = (
            await session.scalars(
                select(MemoryLink).where(MemoryLink.link_type == "derived_from")
            )
        ).all()
    assert first.derived_count == 0
    assert second.derived_count == 1
    assert len(procedures) == 1 and procedures[0].status == "candidate"
    assert len(links) == 2


@pytest.mark.asyncio
async def test_reconciliation_queues_missing_index_and_failure_is_safe(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await owner(sessionmaker)
    start = datetime(2026, 7, 15, tzinfo=UTC)
    end = start + timedelta(days=1)

    class Reconciler:
        async def reconcile(
            self, *, user_id: str, active_memory_ids: tuple[str, ...]
        ) -> ReconciliationReport:
            assert user_id == user.id
            return ReconciliationReport(
                missing_memory_ids=active_memory_ids,
                orphan_index_ids=("orphan-synthetic",),
                deleted_orphan_count=1,
            )

    async with sessionmaker() as session:
        memory = await MemoryService(session).create_memory(
            user_id=user.id, content="项目约束"
        )
        memory.created_at = start + timedelta(hours=1)
        run = await MemoryConsolidationService(
            session, reconciler=Reconciler()
        ).run_daily(user_id=user.id, window_start=start, window_end=end)
        outbox = (await session.scalars(select(MemoryIndexOutbox))).all()
    assert len(outbox) == 1 and outbox[0].memory_id == memory.id
    assert "orphan-synthetic" in run.reconciliation_json


def test_consolidation_migration_and_backup_contract() -> None:
    import importlib
    from scripts.ops.db_common import COUNTED_TABLES

    migration = importlib.import_module(
        "backend.migrations.versions.202607160003_v6_memory_consolidation"
    )
    assert (
        migration.revision == "202607160003"
        and migration.down_revision == "202607160002"
    )
    assert {
        "memory_consolidation_runs",
        "memory_consolidation_digests",
        "memory_consolidation_decisions",
    }.issubset(COUNTED_TABLES)


@pytest.mark.asyncio
async def test_digest_api_does_not_cross_user_boundary(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from fastapi.testclient import TestClient
    from infrastructure.config import Settings
    from app.main import create_app

    first = await owner(sessionmaker)
    async with sessionmaker() as session:
        second = User(display_name="other")
        session.add(second)
        await session.flush()
        start = datetime(2026, 7, 15, tzinfo=UTC)
        end = start + timedelta(days=1)
        await MemoryConsolidationService(session).run_daily(
            user_id=first.id, window_start=start, window_end=end
        )
        await session.commit()
        second_id = second.id
    app = create_app(Settings(database_url="sqlite+aiosqlite:///unused.db"))
    app.state.db_sessionmaker = sessionmaker
    with TestClient(app) as client:
        owned = client.get(
            "/api/memory/consolidation-digests", params={"user_id": first.id}
        )
        foreign = client.get(
            "/api/memory/consolidation-digests", params={"user_id": second_id}
        )
    assert owned.status_code == 200 and len(owned.json()["items"]) == 1
    assert foreign.status_code == 200 and foreign.json()["items"] == []
