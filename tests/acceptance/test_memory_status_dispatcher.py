from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domain.models import Base, Memory, Task, TaskStatus, User
from application.memory_service import MemoryService
from application.status_service import StatusService
from agent import AgentHarness, ExecutionOutcome


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/memory-status.db",
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_user(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    display_name: str = "Memory User",
) -> User:
    async with sessionmaker() as session:
        user = User(display_name=display_name)
        session.add(user)
        await session.commit()
        return user


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    task_type: str,
    input_text: str,
    status: TaskStatus = TaskStatus.PENDING,
    result_text: str | None = None,
    error_message: str | None = None,
    created_at: datetime | None = None,
) -> Task:
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="langbot",
            task_type=task_type,
            input_text=input_text,
            status=status.value,
            result_text=result_text,
            error_message=error_message,
        )
        if created_at is not None:
            task.created_at = created_at
            task.updated_at = created_at
        session.add(task)
        await session.commit()
        return task


async def fetch_task(
    sessionmaker: async_sessionmaker[AsyncSession], task_id: str
) -> Task:
    async with sessionmaker() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        return task


async def fetch_memories(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> list[Memory]:
    async with sessionmaker() as session:
        result = await session.scalars(select(Memory).order_by(Memory.created_at))
        return list(result)


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> ExecutionOutcome:
        self.calls.append(kwargs)
        return ExecutionOutcome(
            status=TaskStatus.SUCCESS.value,
            result_text="ok",
            workflow_key=kwargs["plan"].workflow_key,
        )


@pytest.mark.asyncio
async def test_active_memory_summary_excludes_deleted_and_cross_user_records(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await create_user(sessionmaker)
    other_user = await create_user(sessionmaker, display_name="Other User")
    now = datetime(2026, 6, 22, tzinfo=UTC)
    task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="plan",
        input_text="/plan 先给结论再给步骤",
    )

    async with sessionmaker() as session:
        session.add_all(
            [
                Memory(
                    user_id=user.id,
                    memory_type="preference",
                    content="先给结论",
                    is_active=True,
                    confirmed_by_user=True,
                    created_at=now,
                    updated_at=now,
                ),
                Memory(
                    user_id=user.id,
                    memory_type="preference",
                    content="已删除偏好",
                    is_active=False,
                    deleted_at=now + timedelta(minutes=1),
                    created_at=now + timedelta(minutes=1),
                    updated_at=now + timedelta(minutes=1),
                ),
                Memory(
                    user_id=user.id,
                    memory_type="preference",
                    content="再给步骤",
                    is_active=True,
                    confirmed_by_user=True,
                    created_at=now + timedelta(minutes=2),
                    updated_at=now + timedelta(minutes=2),
                ),
                Memory(
                    user_id=other_user.id,
                    memory_type="preference",
                    content="其他用户偏好",
                    is_active=True,
                ),
            ]
        )
        await session.commit()

    executor = RecordingExecutor()
    async with sessionmaker() as session:
        await AgentHarness(
            session=session,
            executor=executor,
        ).execute_task(task.id)

    assert executor.calls[0]["context"].memory_summary == "再给步骤\n先给结论"


@pytest.mark.asyncio
async def test_memory_commands_write_list_delete_and_reject_invalid_inputs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await create_user(sessionmaker)
    write_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="memory",
        input_text="/memory 记住 我喜欢先给结论再给步骤",
    )
    empty_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="memory",
        input_text="/memory 记住",
    )
    unknown_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="memory",
        input_text="/memory 归档 something",
    )

    async with sessionmaker() as session:
        await MemoryService(session).execute_task(write_task.id)
        await MemoryService(session).execute_task(empty_task.id)
        await MemoryService(session).execute_task(unknown_task.id)

    stored_write = await fetch_task(sessionmaker, write_task.id)
    stored_empty = await fetch_task(sessionmaker, empty_task.id)
    stored_unknown = await fetch_task(sessionmaker, unknown_task.id)
    memories = await fetch_memories(sessionmaker)
    assert stored_write.status == TaskStatus.SUCCESS.value
    assert memories[0].content == "我喜欢先给结论再给步骤"
    assert stored_empty.status == TaskStatus.FAILED.value
    assert "内容" in (stored_empty.error_message or "")
    assert stored_unknown.status == TaskStatus.FAILED.value
    assert "不支持" in (stored_unknown.error_message or "")

    list_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="memory",
        input_text="/memory 查看",
    )
    delete_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="memory",
        input_text=f"/memory 删除 {memories[0].id}",
    )
    async with sessionmaker() as session:
        await MemoryService(session).execute_task(list_task.id)
        await MemoryService(session).execute_task(delete_task.id)

    stored_list = await fetch_task(sessionmaker, list_task.id)
    stored_delete = await fetch_task(sessionmaker, delete_task.id)
    memories = await fetch_memories(sessionmaker)
    assert memories[0].id in (stored_list.result_text or "")
    assert stored_delete.status == TaskStatus.SUCCESS.value
    assert memories[0].is_active is False


@pytest.mark.asyncio
async def test_memory_delete_rejects_cross_user_records_without_leaking_content(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_a = await create_user(sessionmaker, display_name="User A")
    user_b = await create_user(sessionmaker, display_name="User B")
    secret_content = "用户B的私密偏好"
    async with sessionmaker() as session:
        memory = Memory(
            user_id=user_b.id,
            memory_type="preference",
            content=secret_content,
            is_active=True,
        )
        session.add(memory)
        await session.commit()
        memory_id = memory.id

    delete_task = await create_task(
        sessionmaker,
        user_id=user_a.id,
        task_type="memory",
        input_text=f"/memory 删除 {memory_id}",
    )
    async with sessionmaker() as session:
        await MemoryService(session).execute_task(delete_task.id)

    stored_task = await fetch_task(sessionmaker, delete_task.id)
    memories = await fetch_memories(sessionmaker)
    assert stored_task.status == TaskStatus.FAILED.value
    assert secret_content not in (stored_task.error_message or "")
    assert memories[0].is_active is True


@pytest.mark.asyncio
async def test_status_default_queries_latest_non_status_task_and_no_prior_task(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await create_user(sessionmaker)
    now = datetime(2026, 6, 22, tzinfo=UTC)
    learn_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="learn",
        input_text="/learn Python Agent",
        status=TaskStatus.SUCCESS,
        result_text="学习总结: Agent",
        created_at=now,
    )
    status_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="status",
        input_text="/status",
        created_at=now + timedelta(minutes=1),
    )
    async with sessionmaker() as session:
        await StatusService(session).execute_task(status_task.id)

    stored_status = await fetch_task(sessionmaker, status_task.id)
    assert stored_status.status == TaskStatus.SUCCESS.value
    assert learn_task.id in (stored_status.result_text or "")
    assert status_task.id not in (stored_status.result_text or "")
    assert "学习总结" in (stored_status.result_text or "")

    user_without_task = await create_user(sessionmaker, display_name="No Prior Task")
    no_prior_status = await create_task(
        sessionmaker,
        user_id=user_without_task.id,
        task_type="status",
        input_text="/status",
    )
    async with sessionmaker() as session:
        await StatusService(session).execute_task(no_prior_status.id)
    stored_no_prior = await fetch_task(sessionmaker, no_prior_status.id)
    assert "暂无可查询" in (stored_no_prior.result_text or "")


@pytest.mark.asyncio
async def test_status_specific_task_query_enforces_ownership(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_a = await create_user(sessionmaker, display_name="User A")
    user_b = await create_user(sessionmaker, display_name="User B")
    own_task = await create_task(
        sessionmaker,
        user_id=user_a.id,
        task_type="plan",
        input_text="/plan own",
        status=TaskStatus.SUCCESS,
        result_text="计划结果",
    )
    other_task = await create_task(
        sessionmaker,
        user_id=user_b.id,
        task_type="daily",
        input_text="/daily private",
        status=TaskStatus.FAILED,
        error_message="用户B的私密错误",
    )
    own_status = await create_task(
        sessionmaker,
        user_id=user_a.id,
        task_type="status",
        input_text=f"/status {own_task.id}",
    )
    cross_user_status = await create_task(
        sessionmaker,
        user_id=user_a.id,
        task_type="status",
        input_text=f"/status {other_task.id}",
    )
    async with sessionmaker() as session:
        await StatusService(session).execute_task(own_status.id)
        await StatusService(session).execute_task(cross_user_status.id)

    stored_own = await fetch_task(sessionmaker, own_status.id)
    stored_cross_user = await fetch_task(sessionmaker, cross_user_status.id)
    assert own_task.id in (stored_own.result_text or "")
    assert stored_cross_user.status == TaskStatus.FAILED.value
    assert "用户B的私密错误" not in (stored_cross_user.error_message or "")
    assert "无权" in (stored_cross_user.error_message or "")
