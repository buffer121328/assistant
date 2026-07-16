from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.scheduler.cron import CronScheduler, ScheduledTaskDefinition
from apps.scheduler.heartbeat import run_v2_maintenance
from assistant_api.config import Settings
from assistant_api.models import (
    Base,
    ScheduledTaskRun,
    Task,
    TaskStatus,
    ToolLog,
    User,
)
from assistant_api.monitoring import (
    PENDING_COMPENSATION_TOOL_NAME,
    RUNNING_TIMEOUT_TOOL_NAME,
    compensate_overdue_pending_tasks,
    fail_timed_out_running_tasks,
)


SECRET_TOKEN = "secret-token-value"
PRIVATE_URL = "https://private.example.invalid/scheduler"


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/scheduler-monitoring.db",
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    status: TaskStatus,
    input_text: str,
) -> Task:
    async with sessionmaker() as session:
        user = User(display_name=f"Scheduler User {status.value}")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type="plan",
            input_text=input_text,
            status=status.value,
        )
        session.add(task)
        await session.commit()
        return task


async def age_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    task_id: str,
    updated_at: datetime,
) -> None:
    async with sessionmaker() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        task.updated_at = updated_at
        task.created_at = updated_at
        await session.commit()


async def fetch_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    task_id: str,
) -> Task:
    async with sessionmaker() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        return task


async def fetch_tool_logs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> list[ToolLog]:
    async with sessionmaker() as session:
        result = await session.scalars(select(ToolLog).order_by(ToolLog.created_at))
        return list(result)


def assert_safe_text(value: str | None) -> None:
    assert value is not None
    assert SECRET_TOKEN not in value
    assert PRIVATE_URL not in value
    assert "Bearer " not in value
    assert "authorization" not in value.lower()
    assert "cookie" not in value.lower()


@pytest.mark.asyncio
async def test_01_timeout_scan_marks_running_task_failed_with_auditable_safe_summary(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(
        sessionmaker,
        status=TaskStatus.RUNNING,
        input_text="/plan 长时间执行",
    )
    now = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    await age_task(
        sessionmaker,
        task_id=task.id,
        updated_at=now - timedelta(seconds=301),
    )

    async with sessionmaker() as session:
        task_ids = await fail_timed_out_running_tasks(
            session=session,
            timeout_seconds=300.0,
            now=now,
            sensitive_values=[SECRET_TOKEN, PRIVATE_URL],
        )

    stored = await fetch_task(sessionmaker, task.id)
    logs = await fetch_tool_logs(sessionmaker)

    assert task_ids == [task.id]
    assert stored.status == TaskStatus.FAILED.value
    assert "超时" in (stored.error_message or "")
    assert logs[0].tool_name == RUNNING_TIMEOUT_TOOL_NAME
    assert logs[0].status == "succeeded"
    assert_safe_text(stored.error_message)
    assert_safe_text(logs[0].output_text)


@pytest.mark.asyncio
async def test_02_pending_compensation_redispatches_overdue_task_only_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(
        sessionmaker,
        status=TaskStatus.PENDING,
        input_text="/plan 等待补偿",
    )
    now = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    await age_task(
        sessionmaker,
        task_id=task.id,
        updated_at=now - timedelta(seconds=121),
    )
    calls: list[str] = []

    async def dispatch_task(task_id: str) -> None:
        calls.append(task_id)

    async with sessionmaker() as session:
        first = await compensate_overdue_pending_tasks(
            session=session,
            delay_seconds=120.0,
            now=now,
            dispatch_task=dispatch_task,
        )
    async with sessionmaker() as session:
        second = await compensate_overdue_pending_tasks(
            session=session,
            delay_seconds=120.0,
            now=now,
            dispatch_task=dispatch_task,
        )

    stored = await fetch_task(sessionmaker, task.id)
    logs = await fetch_tool_logs(sessionmaker)
    async with sessionmaker() as session:
        task_count = await session.scalar(select(func.count()).select_from(Task))

    assert first == [task.id]
    assert second == []
    assert calls == [task.id]
    assert stored.status == TaskStatus.PENDING.value
    assert task_count == 1
    assert [
        log.tool_name
        for log in logs
        if log.tool_name == PENDING_COMPENSATION_TOOL_NAME and log.status == "succeeded"
    ] == [PENDING_COMPENSATION_TOOL_NAME]


@pytest.mark.asyncio
async def test_03_scheduler_scope_stays_state_based_without_agent_tool_execution(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    running_task = await create_task(
        sessionmaker,
        status=TaskStatus.RUNNING,
        input_text="/plan timeout scope",
    )
    pending_task = await create_task(
        sessionmaker,
        status=TaskStatus.PENDING,
        input_text="/plan compensation scope",
    )
    now = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    await age_task(
        sessionmaker,
        task_id=running_task.id,
        updated_at=now - timedelta(seconds=301),
    )
    await age_task(
        sessionmaker,
        task_id=pending_task.id,
        updated_at=now - timedelta(seconds=121),
    )
    calls: list[str] = []

    async def dispatch_task(task_id: str) -> None:
        calls.append(task_id)

    async with sessionmaker() as session:
        await fail_timed_out_running_tasks(
            session=session,
            timeout_seconds=300.0,
            now=now,
        )
    async with sessionmaker() as session:
        await compensate_overdue_pending_tasks(
            session=session,
            delay_seconds=120.0,
            now=now,
            dispatch_task=dispatch_task,
        )

    logs = await fetch_tool_logs(sessionmaker)
    assert calls == [pending_task.id]
    assert {log.tool_name for log in logs} == {
        RUNNING_TIMEOUT_TOOL_NAME,
        PENDING_COMPENSATION_TOOL_NAME,
    }
    assert not any(log.tool_name == "langgraph.executor" for log in logs)


@pytest.mark.asyncio
async def test_04_cron_creates_one_pending_task_per_schedule_slot(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = User(display_name="Scheduled User")
        session.add(user)
        await session.commit()

    calls: list[str] = []

    async def dispatch_task(task_id: str) -> None:
        calls.append(task_id)

    definition = ScheduledTaskDefinition(
        schedule_key="daily-review",
        user_id=user.id,
        platform="scheduler",
        task_type="daily",
        input_text="生成今日回顾",
    )
    scheduled_for = datetime(2026, 7, 13, 0, 0, tzinfo=UTC)

    async with sessionmaker() as session:
        scheduler = CronScheduler(session=session, dispatch_task=dispatch_task)
        first = await scheduler.create_due_task(
            definition=definition,
            scheduled_for=scheduled_for,
        )
    async with sessionmaker() as session:
        scheduler = CronScheduler(session=session, dispatch_task=dispatch_task)
        second = await scheduler.create_due_task(
            definition=definition,
            scheduled_for=scheduled_for,
        )

    async with sessionmaker() as session:
        task_count = await session.scalar(select(func.count()).select_from(Task))
        run_count = await session.scalar(
            select(func.count()).select_from(ScheduledTaskRun)
        )
        stored = await session.get(Task, first.id)
        logs = list(await session.scalars(select(ToolLog)))

    assert second.id == first.id
    assert task_count == 1
    assert run_count == 1
    assert stored is not None
    assert stored.status == TaskStatus.PENDING.value
    assert stored.platform == "scheduler"
    assert calls == [first.id]
    assert not any(
        log.tool_name in {"langgraph.executor", "mcp.adapter", "shell"} for log in logs
    )


def test_05_celery_beat_has_one_v2_maintenance_entry_for_existing_worker() -> None:
    from assistant_api.worker import celery_app

    entries = [
        entry
        for entry in celery_app.conf.beat_schedule.values()
        if entry["task"] == "assistant_api.run_v2_maintenance"
    ]

    assert len(entries) == 1
    assert entries[0]["schedule"] > 0


@pytest.mark.asyncio
async def test_06_worker_heartbeat_composes_bounded_v2_maintenance(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    result = await run_v2_maintenance(
        sessionmaker=sessionmaker,
        settings=Settings(),
        dispatch_task=None,
        now=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )

    logs = await fetch_tool_logs(sessionmaker)

    assert result == {
        "timed_out_task_ids": [],
        "compensated_task_ids": [],
        "archived_memory_ids": [],
        "memory_consolidation": {
            "processed_user_count": 0,
            "daily_run_ids": (),
            "weekly_run_ids": (),
        },
        "evolution_suggestion_created": False,
        "created_notification_outbox_ids": [],
        "delivered_notification_outbox_ids": [],
    }
    assert [log.tool_name for log in logs] == ["memory.maintenance"]
    assert not any(
        log.tool_name in {"langgraph.executor", "mcp.adapter", "shell"} for log in logs
    )
