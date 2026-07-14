from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
import yaml  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from assistant_api.models import Base, Memory, Task, TaskStatus, ToolLog, User
from assistant_api.services import MemoryService
from packages.agent_harness.capabilities import CapabilitiesBuilder, ToolCapability
from packages.agent_harness.context import ContextBuilder
from packages.agent_harness.evolution import (
    EVOLUTION_SUGGESTION_TOOL_NAME,
    BehaviorEvolutionService,
)
from packages.memory.context import load_memory_summary
from packages.memory.maintenance import maintain_memories


ROOT = Path(__file__).resolve().parents[2]
SECRET_TOKEN = "v2-secret-token"
PRIVATE_URL = "https://private.example.invalid/evolution"


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v2-monitoring-memory-evolution.db",
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


@pytest.mark.asyncio
async def test_01_memory_maintenance_archives_expired_and_stale_working_only(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    async with sessionmaker() as session:
        user = User(display_name="Memory Lifecycle User")
        session.add(user)
        await session.flush()
        expired = Memory(
            user_id=user.id,
            memory_type="preference",
            content="explicitly expired",
            expires_at=now - timedelta(seconds=1),
            importance_score=10,
        )
        stale_working = Memory(
            user_id=user.id,
            memory_type="working",
            content="stale low value",
            importance_score=1,
            access_count=0,
            updated_at=now - timedelta(days=31),
        )
        durable_preference = Memory(
            user_id=user.id,
            memory_type="preference",
            content="durable preference",
            importance_score=1,
            access_count=0,
            updated_at=now - timedelta(days=31),
        )
        session.add_all([expired, stale_working, durable_preference])
        await session.commit()

        result = await maintain_memories(
            session=session,
            now=now,
            stale_after_days=30,
            max_stale_importance=1,
            max_stale_access_count=0,
        )
        await session.commit()

    async with sessionmaker() as session:
        stored_expired = await session.get(Memory, expired.id)
        stored_working = await session.get(Memory, stale_working.id)
        stored_preference = await session.get(Memory, durable_preference.id)

    assert set(result.archived_memory_ids) == {expired.id, stale_working.id}
    assert stored_expired is not None and stored_expired.archived_at is not None
    assert stored_expired.archived_at.replace(tzinfo=UTC) == now
    assert stored_expired.deleted_at is None and stored_expired.is_active is False
    assert stored_working is not None and stored_working.archived_at is not None
    assert stored_working.archived_at.replace(tzinfo=UTC) == now
    assert stored_preference is not None and stored_preference.archived_at is None
    assert stored_preference.is_active is True
    assert stored_preference.content == "durable preference"


@pytest.mark.asyncio
async def test_02_agent_context_filters_memory_and_only_context_counts_access(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    async with sessionmaker() as session:
        user = User(display_name="Memory Context User")
        session.add(user)
        await session.flush()
        eligible = Memory(user_id=user.id, content="eligible memory")
        deleted = Memory(
            user_id=user.id,
            content="deleted memory",
            is_active=False,
            deleted_at=now,
        )
        archived = Memory(
            user_id=user.id,
            content="archived memory",
            is_active=False,
            archived_at=now,
        )
        expired = Memory(
            user_id=user.id,
            content="expired memory",
            expires_at=now - timedelta(days=1),
        )
        session.add_all([eligible, deleted, archived, expired])
        await session.commit()

        summary = await load_memory_summary(session=session, user_id=user.id, now=now)
        await session.commit()

    assert "eligible memory" in summary
    assert "deleted memory" not in summary
    assert "archived memory" not in summary
    assert "expired memory" not in summary

    async with sessionmaker() as session:
        stored = await session.get(Memory, eligible.id)
        assert stored is not None
        assert stored.access_count == 1
        assert stored.last_accessed_at is not None
        assert stored.last_accessed_at.replace(tzinfo=UTC) == now
        listed = await MemoryService(session).list_active_memories(user.id)

    assert [memory.id for memory in listed] == [eligible.id]
    async with sessionmaker() as session:
        stored = await session.get(Memory, eligible.id)
        assert stored is not None
        assert stored.access_count == 1


def test_03_capability_refresh_is_revisioned_and_context_uses_latest_snapshot() -> None:
    builder = CapabilitiesBuilder(
        (
            ToolCapability(
                name="search",
                description="Search the web",
                enabled=True,
            ),
        )
    )
    first = builder.build(requested_tools=("search", "unknown"))
    builder.refresh(
        (
            ToolCapability(
                name="search",
                description="Search the web",
                enabled=False,
            ),
            ToolCapability(
                name="calendar",
                description="Read a calendar",
                enabled=True,
            ),
        )
    )
    second = builder.build(requested_tools=("search", "calendar", "unknown"))
    task = type(
        "TaskStub",
        (),
        {"id": "task-1", "user_id": "user-1", "task_type": "plan", "input_text": "plan"},
    )()
    user = type("UserStub", (), {"id": "user-1", "display_name": "User"})()
    context = ContextBuilder().build(
        task=task,
        user=user,
        memory_summary="",
        skills=(),
        capabilities=second,
    )

    assert first.revision == 0
    assert first.allowed_tools == ("search",)
    assert second.revision == 1
    assert second.allowed_tools == ("calendar",)
    assert context.capability_revision == 1


@pytest.mark.asyncio
async def test_04_evolution_suggestion_is_safe_deduplicated_and_waits_for_approval(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    prompt_files = sorted((ROOT / "prompts" / "skills").rglob("*"))
    before = {
        path: path.read_bytes()
        for path in prompt_files
        if path.is_file()
    }

    async with sessionmaker() as session:
        user = User(display_name="Evolution User")
        session.add(user)
        await session.flush()
        session.add_all(
            [
                Task(
                    user_id=user.id,
                    platform="api",
                    task_type="plan",
                    input_text=f"failed input {SECRET_TOKEN} {PRIVATE_URL}",
                    status=TaskStatus.FAILED.value,
                    created_at=now,
                ),
                Task(
                    user_id=user.id,
                    platform="api",
                    task_type="plan",
                    input_text="needs approval",
                    status=TaskStatus.WAITING_APPROVAL.value,
                    created_at=now,
                ),
                Task(
                    user_id=user.id,
                    platform="api",
                    task_type="plan",
                    input_text="future failure must be ignored",
                    status=TaskStatus.FAILED.value,
                    created_at=now + timedelta(days=1),
                ),
            ]
        )
        session.add(
            ToolLog(
                tool_name="unsafe.example",
                status="failed",
                input_text=f"Bearer {SECRET_TOKEN}",
                error_message=f"private endpoint {PRIVATE_URL}",
                created_at=now,
            )
        )
        session.add(
            ToolLog(
                tool_name="future.example",
                status="failed",
                input_text="future tool log must be ignored",
                created_at=now + timedelta(days=1),
            )
        )
        await session.commit()

        service = BehaviorEvolutionService(session)
        first = await service.evaluate(now=now)
        second = await service.evaluate(now=now)
        await session.commit()

    async with sessionmaker() as session:
        suggestions = list(
            await session.scalars(
                select(ToolLog).where(
                    ToolLog.tool_name == EVOLUTION_SUGGESTION_TOOL_NAME
                )
            )
        )

    after = {
        path: path.read_bytes()
        for path in prompt_files
        if path.is_file()
    }

    assert first is not None
    assert second is None
    assert first.metrics.task_count == 2
    assert first.metrics.failed_task_count == 1
    assert first.metrics.waiting_approval_task_count == 1
    assert first.metrics.failed_tool_log_count == 1
    assert len(suggestions) == 1
    assert suggestions[0].status == TaskStatus.WAITING_APPROVAL.value
    safe_text = " ".join(
        value or ""
        for value in (
            suggestions[0].input_text,
            suggestions[0].output_text,
            suggestions[0].error_message,
        )
    )
    assert SECRET_TOKEN not in safe_text
    assert PRIVATE_URL not in safe_text
    assert "Bearer " not in safe_text
    assert before == after


def test_05_v2_04_runtime_and_readme_match_the_reviewable_boundary() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    beat_services = [
        service
        for service in compose["services"].values()
        if "beat" in service.get("command", [])
    ]

    assert "SCHEDULER_MAINTENANCE_INTERVAL_SECONDS=" in env_example
    assert len(beat_services) == 1
    assert beat_services[0]["command"][:4] == [
        "celery",
        "-A",
        "assistant_api.worker:celery_app",
        "beat",
    ]
    assert "V2-04" in readme
    assert "Celery Beat" in readme
    assert "TaskService" in readme
    assert "单实例" in readme
    assert "access_count" in readme
    assert "waiting_approval" in readme
    assert "不会自动修改" in readme
    assert "V2-05" in readme
    assert "V3-08 已移除 Deepeval" in readme
