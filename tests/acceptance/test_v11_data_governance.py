from __future__ import annotations

from collections.abc import AsyncIterator
import importlib
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domain.models import AgentRun, Base, ModelLog, ProcessedMessage, Task, User
from infrastructure.repositories import MessageRepository, ModelLogCreate, ModelLogRepository


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v11-data-governance.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_processed_message_uniqueness_includes_adapter(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        session.add_all(
            [
                ProcessedMessage(
                    platform="langbot",
                    adapter="discord",
                    message_id="shared-message",
                    reason="task_created",
                ),
                ProcessedMessage(
                    platform="langbot",
                    adapter="telegram",
                    message_id="shared-message",
                    reason="task_created",
                ),
            ]
        )
        await session.commit()

    async with sessionmaker() as session:
        repository = MessageRepository(session)
        discord = await repository.get_processed_message(
            platform="langbot",
            adapter="discord",
            message_id="shared-message",
        )
        telegram = await repository.get_processed_message(
            platform="langbot",
            adapter="telegram",
            message_id="shared-message",
        )
        assert discord is not None and discord.adapter == "discord"
        assert telegram is not None and telegram.adapter == "telegram"

        session.add(
            ProcessedMessage(
                platform="langbot",
                adapter="discord",
                message_id="shared-message",
                reason="task_created",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_model_log_agent_run_association_is_optional(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = User(display_name="V11 User")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type="plan",
            input_text="associate logs",
            status="running",
        )
        session.add(task)
        await session.flush()
        run = AgentRun(
            task_id=task.id,
            user_id=user.id,
            attempt_no=1,
            status="running",
        )
        session.add(run)
        await session.flush()
        repository = ModelLogRepository(session)
        associated = await repository.create_model_log(
            ModelLogCreate(
                task_id=task.id,
                agent_run_id=run.id,
                model_class="standard",
                request_text="request",
                response_text="response",
                error_message=None,
            )
        )
        unassociated = await repository.create_model_log(
            ModelLogCreate(
                task_id=task.id,
                model_class="standard",
                request_text="direct request",
                response_text="direct response",
                error_message=None,
            )
        )
        await session.commit()

    assert associated.agent_run_id == run.id
    assert unassociated.agent_run_id is None
    async with sessionmaker() as session:
        stored = list(await session.scalars(select(ModelLog).order_by(ModelLog.created_at)))
    assert [item.agent_run_id for item in stored] == [run.id, None]


def test_v11_data_governance_migration_is_linear_and_reversible() -> None:
    migration = importlib.import_module(
        "backend.migrations.versions.202607210001_v11_data_governance_foundation"
    )

    assert migration.revision == "202607210001"
    assert migration.down_revision == "202607190002"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)
