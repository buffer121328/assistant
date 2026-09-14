from __future__ import annotations

from collections.abc import AsyncIterator
import json
from pathlib import Path

import pytest
from sqlalchemy import select
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from application.session_context.conversations import ConversationService
from application.session_context.resource_references import ResourceReferenceError, ResourceReferenceService
from application.task_execution.lifecycle import InvalidCommandTaskError, TaskService
from domain.models import Base, TaskContextSnapshot, Tenant, User
from infrastructure.settings.config import Settings


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/commands.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def client(sessionmaker: async_sessionmaker[AsyncSession]) -> TestClient:
    app = create_app(
        Settings(
            database_url="sqlite+aiosqlite:///unused.db",
            redis_url="redis://placeholder",
        )
    )
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


async def create_user(
    sessionmaker: async_sessionmaker[AsyncSession], name: str
) -> User:
    async with sessionmaker() as session:
        if await session.get(Tenant, "local") is None:
            session.add(Tenant(id="local", name="Local"))
            await session.flush()
        user = User(display_name=name, tenant_id="local")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.mark.asyncio
async def test_command_catalog_and_structured_task_selection_are_server_owned(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await create_user(sessionmaker, "Composer user")

    catalog = client.get("/local/commands/catalog", params={"user_id": user.id})
    assert catalog.status_code == 200
    items = catalog.json()["items"]
    assert {item["command_id"] for item in items} >= {
        "plan",
        "learn",
        "daily",
        "office",
        "memory",
        "status",
        "screen",
    }
    assert all(item["selectable"] is True for item in items)
    assert all(
        internal not in catalog.text
        for internal in ("Skill", "MCP", "Connector", "Executor")
    )

    created = client.post(
        "/local/tasks",
        json={
            "user_id": user.id,
            "task_type": "plan",
            "command_id": "plan",
            "input_text": "制定本周计划",
        },
    )
    assert created.status_code == 201
    task_id = created.json()["task"]["task_id"]

    async with sessionmaker() as session:
        snapshot = await session.scalar(
            select(TaskContextSnapshot).where(
                TaskContextSnapshot.task_id == task_id
            )
        )
    assert snapshot is not None
    assert snapshot.command_id == "plan"

    unknown = client.post(
        "/local/tasks",
        json={
            "user_id": user.id,
            "task_type": "office",
            "command_id": "send-money",
            "input_text": "未知动作",
        },
    )
    assert unknown.status_code == 400
    assert "unknown_command" in unknown.text


@pytest.mark.asyncio
async def test_task_service_rejects_unknown_inline_slash_but_keeps_natural_language_default(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await create_user(sessionmaker, "Natural language user")
    async with sessionmaker() as session:
        natural = await TaskService(session).create_task(
            user_id=user.id,
            platform="local",
            task_type="office",
            input_text="帮我把这段话改得更礼貌",
        )
        assert natural.id
        with pytest.raises(InvalidCommandTaskError, match="unknown_command"):
            await TaskService(session).create_task(
                user_id=user.id,
                platform="local",
                task_type="office",
                input_text="/send-money 转账",
            )


@pytest.mark.asyncio
async def test_mention_selection_reauthorizes_reference_at_task_creation(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    user = await create_user(sessionmaker, "Mention user")
    async with sessionmaker() as session:
        conversation = await ConversationService(session).create(user_id=user.id)
        source = tmp_path / "customer-update.txt"
        source.write_text("Customer update", encoding="utf-8")
        reference = await ResourceReferenceService(session).attach_local_file(
            conversation_id=conversation.id,
            user_id=user.id,
            source_ref=str(source),
        )
        task = await TaskService(session).create_task(
            user_id=user.id,
            platform="local",
            task_type="office",
            input_text="根据材料写一封邮件",
            conversation_id=conversation.id,
            resource_reference_ids=(reference.id,),
        )
        assert task.conversation_id == conversation.id
        snapshot = await session.scalar(
            select(TaskContextSnapshot).where(
                TaskContextSnapshot.task_id == task.id
            )
        )
        assert snapshot is not None
        assert json.loads(snapshot.resource_reference_ids_json) == [reference.id]

@pytest.mark.asyncio
async def test_catalog_and_resource_selection_fail_closed_for_unknown_or_forged_identity(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    owner = await create_user(sessionmaker, "Reference owner")
    foreign = await create_user(sessionmaker, "Foreign user")

    missing = client.get("/local/commands/catalog", params={"user_id": "missing-user"})
    assert missing.status_code == 404

    async with sessionmaker() as session:
        conversation = await ConversationService(session).create(user_id=owner.id)
        source = tmp_path / "owner-only.txt"
        source.write_text("owner material", encoding="utf-8")
        reference = await ResourceReferenceService(session).attach_local_file(
            conversation_id=conversation.id,
            user_id=owner.id,
            source_ref=str(source),
        )

    forged = client.post(
        "/local/tasks",
        json={
            "user_id": foreign.id,
            "task_type": "office",
            "input_text": "use forged context",
            "conversation_id": conversation.id,
            "resource_reference_ids": [reference.id],
        },
    )
    assert forged.status_code in {403, 404}
    assert "resource_reference" in forged.text or "conversation" in forged.text


@pytest.mark.asyncio
async def test_snapshot_reauthorization_rejects_stale_local_file(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    user = await create_user(sessionmaker, "Stale reference user")
    async with sessionmaker() as session:
        conversation = await ConversationService(session).create(user_id=user.id)
        source = tmp_path / "stale.txt"
        source.write_text("before", encoding="utf-8")
        reference = await ResourceReferenceService(session).attach_local_file(
            conversation_id=conversation.id,
            user_id=user.id,
            source_ref=str(source),
        )
        source.write_text("after", encoding="utf-8")
        with pytest.raises(ResourceReferenceError, match="resource_reference_stale"):
            await TaskService(session).create_task(
                user_id=user.id,
                platform="local",
                task_type="office",
                input_text="use current material",
                conversation_id=conversation.id,
                resource_reference_ids=(reference.id,),
            )
