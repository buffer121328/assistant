from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from infrastructure.settings.config import Settings
from app.main import create_app
from domain.models import Base, TaskStatus, User
from tasks.lifecycle import TaskService


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/conversation.db", poolclass=NullPool
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


@pytest.mark.asyncio
async def test_conversation_task_messages_and_owner_isolation(
    client: TestClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    owner = await create_user(sessionmaker, "Owner")
    other = await create_user(sessionmaker, "Other")

    created = client.post(
        "/api/conversations", json={"user_id": owner.id, "title": "项目讨论"}
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]

    submitted = client.post(
        "/api/tasks/submit",
        json={
            "user_id": owner.id,
            "platform": "desktop",
            "task_type": "plan",
            "input_text": "继续讨论第一步",
            "conversation_id": conversation_id,
        },
    )
    assert submitted.status_code == 201
    task_id = submitted.json()["task"]["task_id"]
    assert submitted.json()["task"]["conversation_id"] == conversation_id

    async with sessionmaker() as session:
        service = TaskService(session)
        await service.update_status(task_id, TaskStatus.RUNNING)
        task = await service.save_success(task_id, "这是助手回答")
        assert task.conversation_id == conversation_id

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        params={"user_id": owner.id},
    )
    denied = client.get(
        f"/api/conversations/{conversation_id}/messages",
        params={"user_id": other.id},
    )
    cross_submit = client.post(
        "/api/tasks/submit",
        json={
            "user_id": other.id,
            "platform": "desktop",
            "task_type": "plan",
            "input_text": "不应写入",
            "conversation_id": conversation_id,
        },
    )

    assert [(item["role"], item["content"]) for item in messages.json()["items"]] == [
        ("user", "继续讨论第一步"),
        ("assistant", "这是助手回答"),
    ]
    assert denied.status_code == 404
    assert cross_submit.status_code == 404


@pytest.mark.asyncio
async def test_archived_conversation_is_hidden_and_cannot_continue(
    client: TestClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    user = await create_user(sessionmaker, "Archive User")
    created = client.post("/api/conversations", json={"user_id": user.id})
    conversation_id = created.json()["conversation_id"]

    archived = client.post(
        f"/api/conversations/{conversation_id}/archive", json={"user_id": user.id}
    )
    listed = client.get("/api/conversations", params={"user_id": user.id})
    submitted = client.post(
        "/api/tasks/submit",
        json={
            "user_id": user.id,
            "platform": "desktop",
            "task_type": "status",
            "input_text": "归档后继续",
            "conversation_id": conversation_id,
        },
    )

    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert listed.json()["items"] == []
    assert submitted.status_code == 404


def test_desktop_client_conversation_contract() -> None:
    import httpx
    from assistant_desktop.client import DesktopApiClient

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/conversations" and request.method == "POST":
            return httpx.Response(201, json={"conversation_id": "conversation-1"})
        if request.url.path == "/api/conversations":
            return httpx.Response(
                200, json={"items": [{"conversation_id": "conversation-1"}]}
            )
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200, json={"items": [{"role": "user", "content": "hi"}]}
            )
        if request.url.path.endswith("/archive"):
            return httpx.Response(200, json={"conversation_id": "conversation-1"})
        return httpx.Response(
            201,
            json={
                "task": {"task_id": "task-1", "conversation_id": "conversation-1"},
                "queued": False,
            },
        )

    client = DesktopApiClient(
        base_url="http://127.0.0.1:8000",
        user_id="user-1",
        transport=httpx.MockTransport(handler),
    )
    client.create_conversation()
    client.list_conversations()
    client.list_conversation_messages("conversation-1")
    client.archive_conversation("conversation-1")
    client.submit_task(
        task_type="plan", input_text="继续", conversation_id="conversation-1"
    )

    assert [request.url.path for request in requests] == [
        "/api/conversations",
        "/api/conversations",
        "/api/conversations/conversation-1/messages",
        "/api/conversations/conversation-1/archive",
        "/api/tasks/submit",
    ]
    assert b'"conversation_id":"conversation-1"' in requests[-1].content
    client.close()


@pytest.mark.asyncio
async def test_langbot_reuses_conversation_per_user(
    client: TestClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from sqlalchemy import select
    from domain.models import Conversation, PlatformAccount, Task

    first = await create_user(sessionmaker, "LangBot One")
    second = await create_user(sessionmaker, "LangBot Two")
    async with sessionmaker() as session:
        session.add_all(
            [
                PlatformAccount(
                    user_id=first.id,
                    platform="langbot",
                    platform_user_id="discord:sender-1",
                ),
                PlatformAccount(
                    user_id=second.id,
                    platform="langbot",
                    platform_user_id="discord:sender-2",
                ),
            ]
        )
        await session.commit()

    def payload(message_id: str, sender_id: str) -> dict[str, object]:
        return {
            "message_id": message_id,
            "adapter": "discord",
            "conversation": {"id": "group-1", "type": "group"},
            "sender": {"id": sender_id},
            "message": {"type": "text", "text": "/status"},
        }

    headers = {"X-LangBot-Secret": "placeholder-langbot-webhook-secret"}
    assert (
        client.post(
            "/api/webhooks/langbot", json=payload("m-1", "sender-1"), headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/webhooks/langbot", json=payload("m-2", "sender-1"), headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/webhooks/langbot", json=payload("m-3", "sender-2"), headers=headers
        ).status_code
        == 200
    )

    async with sessionmaker() as session:
        conversations = list(await session.scalars(select(Conversation)))
        tasks = list(await session.scalars(select(Task).order_by(Task.created_at)))

    assert len(conversations) == 2
    assert tasks[0].conversation_id == tasks[1].conversation_id
    assert tasks[2].conversation_id != tasks[0].conversation_id


def test_agent_model_request_uses_prebudgeted_conversation_history_without_second_slice() -> (
    None
):
    from agent.modeling.agent_model import build_agent_model_request
    from agent.planning.context import TaskContext
    from agent.modeling.executors import AgentRunInput
    from agent.planning.planner import ExecutionPlan

    context = TaskContext(
        task_id="task-1",
        user_id="user-1",
        task_type="plan",
        input_text="current",
        memory_summary="",
        conversation_history=tuple(
            ("user" if index % 2 == 0 else "assistant", f"history-{index}")
            for index in range(15)
        ),
    )
    plan = ExecutionPlan(
        goal="goal",
        steps=("step",),
        allowed_tools=(),
        approval_required_tools=(),
        max_steps=2,
        timeout_seconds=30,
        risk_level="low",
        output_format="text",
        profile_name="test",
        executor_kind="langgraph",
        workflow_key="test",
    )

    request = build_agent_model_request(
        AgentRunInput(plan=plan, context=context), tool_schemas=()
    )

    assert [message.content for message in request.messages[1:-1]] == [
        f"history-{index}" for index in range(15)
    ]
    assert request.messages[-1].content == "current"


@pytest.mark.asyncio
async def test_conversation_message_api_reports_active_compaction_metadata(
    client: TestClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from session.conversations import ConversationService
    from domain.models import ConversationSummary

    owner = await create_user(sessionmaker, "Compacted Owner")
    async with sessionmaker() as session:
        conversation = await ConversationService(session).create(
            user_id=owner.id, commit=False
        )
        first = await ConversationService(session).append_message(
            conversation_id=conversation.id,
            user_id=owner.id,
            role="user",
            content="最早约束",
        )
        last = await ConversationService(session).append_message(
            conversation_id=conversation.id,
            user_id=owner.id,
            role="assistant",
            content="当前进展",
        )
        summary = ConversationSummary(
            conversation_id=conversation.id,
            user_id=owner.id,
            summary_text="当前目标: 保留约束",
            content_json="{}",
            source_start_message_id=first.id,
            source_end_message_id=last.id,
            source_message_count=2,
            summary_version="summary-v1",
            model_version="synthetic-model",
            status="active",
        )
        session.add(summary)
        await session.commit()
        conversation_id = conversation.id

    response = client.get(
        f"/api/conversations/{conversation_id}/messages",
        params={"user_id": owner.id},
    )

    assert response.status_code == 200
    assert response.json()["compacted"] is True
    assert response.json()["summary_version"] == "summary-v1"
    assert response.json()["summary_updated_at"] is not None


def test_local_conversation_token_stats_and_session_continuation(
    client: TestClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    import anyio

    owner = anyio.run(create_user, sessionmaker, "Token Owner")

    first = client.post(
        "/local/tasks",
        json={
            "user_id": owner.id,
            "task_type": "plan",
            "input_text": "你好，请做计划",
            "model_class": "light",
        },
    )
    assert first.status_code == 201
    first_task = first.json()["task"]
    conversation_id = first_task["conversation_id"]
    assert conversation_id

    second = client.post(
        f"/local/tasks/{first_task['task_id']}/messages",
        json={"user_id": owner.id, "content": "继续完善第二步"},
    )
    assert second.status_code == 200
    second_task = second.json()["task"]
    assert second_task["conversation_id"] == conversation_id
    assert second_task["task_id"] != first_task["task_id"]

    stats = client.get(
        f"/local/conversations/{conversation_id}/token-stats",
        params={"user_id": owner.id},
    )
    assert stats.status_code == 200
    payload = stats.json()
    assert payload["conversation_id"] == conversation_id
    assert payload["message_count"] == 2
    assert payload["user_message_count"] == 2
    assert payload["assistant_message_count"] == 0
    assert payload["total_estimated_tokens"] > 0
    assert payload["token_limit"] == 4000
    assert payload["status"] == "ok"

    denied = client.get(
        f"/local/conversations/{conversation_id}/token-stats",
        params={"user_id": "not-owner"},
    )
    assert denied.status_code == 404
