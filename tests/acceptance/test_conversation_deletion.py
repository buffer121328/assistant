from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from application.session_context.conversations import ConversationError, ConversationService
from domain.models import Base, User
from infrastructure.settings.config import Settings


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/conversation-deletion.db", poolclass=NullPool
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
        user = User(display_name=name)
        session.add(user)
        await session.commit()
        return user


def create_conversation_task(client: TestClient, user_id: str) -> tuple[str, str]:
    created = client.post("/api/conversations", json={"user_id": user_id, "title": "待删除会话"})
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]
    submitted = client.post(
        "/api/tasks/submit",
        json={
            "user_id": user_id,
            "platform": "desktop",
            "task_type": "plan",
            "input_text": "需要删除的会话",
            "conversation_id": conversation_id,
        },
    )
    assert submitted.status_code == 201
    return conversation_id, submitted.json()["task"]["task_id"]


@pytest.mark.asyncio
async def test_local_task_delete_archives_linked_conversation_and_hides_its_tasks(
    client: TestClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    user = await create_user(sessionmaker, "Conversation Delete Owner")
    conversation_id, task_id = create_conversation_task(client, user.id)

    deleted = client.delete(f"/local/tasks/{task_id}", params={"user_id": user.id})
    listed = client.get("/local/tasks", params={"user_id": user.id})
    conversations = client.get("/api/conversations", params={"user_id": user.id})

    assert deleted.status_code == 204
    assert listed.json()["items"] == []
    assert conversations.json()["items"] == []
    assert conversation_id


@pytest.mark.asyncio
async def test_local_task_delete_returns_the_conversation_archive_failure(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(sessionmaker, "Conversation Archive Failure")
    conversation_id, task_id = create_conversation_task(client, user.id)

    async def reject_archive(
        _self: ConversationService, *, conversation_id: str, user_id: str
    ) -> None:
        raise ConversationError("conversation_archive_unavailable", status_code=409)

    monkeypatch.setattr(ConversationService, "archive", reject_archive)

    deleted = client.delete(f"/local/tasks/{task_id}", params={"user_id": user.id})
    conversations = client.get("/api/conversations", params={"user_id": user.id})

    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "conversation_archive_unavailable"
    assert [item["conversation_id"] for item in conversations.json()["items"]] == [conversation_id]
