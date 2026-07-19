from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent.tool_management.schedule_tools import AgentScheduleService
from agent.tool_management.task_tools import AgentTaskToolService
from domain.models import AgentScheduleRun, Base, Conversation, Task, TaskEvent, TaskStatus, ToolLog, User


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/task-schedule.db", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def user_and_conversation(session: AsyncSession) -> tuple[User, Conversation]:
    user = User(display_name="Tool User")
    session.add(user)
    await session.flush()
    conversation = Conversation(user_id=user.id, title="Task", channel="local", external_key=None)
    session.add(conversation)
    await session.flush()
    return user, conversation


@pytest.mark.asyncio
async def test_task_start_background_returns_task_id_and_queued_event(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with sessionmaker() as session:
        user, conversation = await user_and_conversation(session)
        result = await AgentTaskToolService(session).start_background(
            user_id=user.id,
            conversation_id=conversation.id,
            task_type="learn",
            input_text="/learn background",
        )
        events = list((await session.scalars(select(TaskEvent))).all())
        logs = list((await session.scalars(select(ToolLog))).all())

    assert result["task_id"]
    assert result["status"] == TaskStatus.PENDING.value
    assert events[0].event_type == "queued"
    assert logs[0].tool_name == "task.start_background"


@pytest.mark.asyncio
async def test_task_check_get_and_cancel_are_owner_scoped_and_bounded(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with sessionmaker() as session:
        user, conversation = await user_and_conversation(session)
        other = User(display_name="Other")
        session.add(other)
        await session.flush()
        task = Task(user_id=user.id, platform="api", task_type="learn", input_text="work", conversation_id=conversation.id, status=TaskStatus.RUNNING.value)
        session.add(task)
        await session.flush()
        for index in range(5):
            session.add(TaskEvent(task_id=task.id, user_id=user.id, event_type="progress", sequence=index + 1, payload_json='{"ok":true}'))
        await session.flush()
        service = AgentTaskToolService(session, max_events=3, max_result_chars=10)

        status = await service.check_status(user_id=user.id, task_id=task.id)
        with pytest.raises(Exception):
            await service.check_status(user_id=other.id, task_id=task.id)
        cancel = await service.cancel(user_id=user.id, task_id=task.id, reason="stop now")
        again = await service.cancel(user_id=user.id, task_id=task.id, reason="again")

    events = status["events"]
    assert isinstance(events, list)
    assert len(events) == 3
    assert cancel["cancelled"] is True
    assert again["cancelled"] is False
    assert again["reason"] == "terminal"


@pytest.mark.asyncio
async def test_schedule_at_materializes_once_idempotently(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    async with sessionmaker() as session:
        user, conversation = await user_and_conversation(session)
        service = AgentScheduleService(session)
        schedule = await service.create(
            user_id=user.id,
            conversation_id=conversation.id,
            mode="at",
            payload={"task_type": "learn", "input_text": "/learn once"},
            run_at=now,
        )
        runs_1 = await service.materialize_due(now=now + timedelta(minutes=1))
        runs_2 = await service.materialize_due(now=now + timedelta(minutes=2))
        db_runs = list((await session.scalars(select(AgentScheduleRun))).all())

    assert len(runs_1) == 1
    assert runs_2 == []
    assert len(db_runs) == 1
    assert schedule.enabled is False


@pytest.mark.asyncio
async def test_schedule_every_skip_policy_advances_without_unbounded_catchup(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with sessionmaker() as session:
        user, conversation = await user_and_conversation(session)
        service = AgentScheduleService(session)
        schedule = await service.create(
            user_id=user.id,
            conversation_id=conversation.id,
            mode="every",
            payload={"task_type": "daily", "input_text": "/daily every"},
            every_seconds=60,
            catch_up_policy="skip",
        )
        assert schedule.next_run_at is not None
        first = schedule.next_run_at
        runs = await service.materialize_due(now=first + timedelta(minutes=10))

    assert len(runs) == 1
    assert schedule.next_run_at is not None
    first_utc = first if first.tzinfo else first.replace(tzinfo=UTC)
    next_run = schedule.next_run_at if schedule.next_run_at.tzinfo else schedule.next_run_at.replace(tzinfo=UTC)
    assert next_run > first_utc + timedelta(minutes=10)


@pytest.mark.asyncio
async def test_schedule_cron_requires_iana_timezone_and_computes_next_run(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with sessionmaker() as session:
        user, conversation = await user_and_conversation(session)
        service = AgentScheduleService(session)
        with pytest.raises(ValueError):
            await service.create(user_id=user.id, conversation_id=conversation.id, mode="cron", payload={"input_text": "bad"}, cron_expr="0 9 * * *", timezone="Not/AZone")
        schedule = await service.create(user_id=user.id, conversation_id=conversation.id, mode="cron", payload={"input_text": "ok"}, cron_expr="0 9 * * *", timezone="Asia/Shanghai")

    assert schedule.next_run_at is not None
    assert schedule.timezone == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_schedule_owner_scope_history_toggle_run_now_delete(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with sessionmaker() as session:
        user, conversation = await user_and_conversation(session)
        other = User(display_name="Other")
        session.add(other)
        await session.flush()
        service = AgentScheduleService(session)
        schedule = await service.create(user_id=user.id, conversation_id=conversation.id, mode="every", payload={"input_text": "owned"}, every_seconds=60)
        assert len(await service.list_schedules(user_id=user.id)) == 1
        with pytest.raises(ValueError):
            await service.toggle(user_id=other.id, schedule_id=schedule.id, enabled=False)
        toggled = await service.toggle(user_id=user.id, schedule_id=schedule.id, enabled=False)
        run = await service.run_now(user_id=user.id, schedule_id=schedule.id)
        history = await service.history(user_id=user.id, schedule_id=schedule.id)
        deleted = await service.delete(user_id=user.id, schedule_id=schedule.id)

    assert toggled["enabled"] is False
    assert run["task_id"]
    assert len(history) == 1
    assert deleted["deleted"] is True
