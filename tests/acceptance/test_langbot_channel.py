from __future__ import annotations

from collections.abc import AsyncIterator
import json
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from infrastructure.config import Settings
from app.main import create_app
from domain.models import Base, PlatformAccount, ProcessedMessage, Task, TaskStatus, ToolLog, User
from domain.services import ResultDispatcher


WEBHOOK_PATH = "/api/webhooks/langbot"
LANGBOT_WEBHOOK_SECRET = "test-langbot-webhook-secret"


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/langbot.db",
        poolclass=NullPool,
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
def client(sessionmaker: async_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "channels.langbot.service.enqueue_task_execution",
        lambda _task_id, **_kwargs: True,
    )
    settings = Settings(
        database_url="sqlite+aiosqlite:///unused.db",
        redis_url="redis://placeholder",
        langbot_webhook_secret=LANGBOT_WEBHOOK_SECRET,
    )
    app = create_app(settings)
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


def langbot_headers(*, secret: str = LANGBOT_WEBHOOK_SECRET) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-langbot-secret": secret,
    }


def langbot_payload(
    text: str,
    *,
    message_id: str = "lb_msg_1",
    adapter: str = "discord",
    sender_id: str = "sender_1",
    conversation_id: str = "conv_1",
    conversation_type: str = "group",
) -> dict[str, object]:
    return {
        "message_id": message_id,
        "adapter": adapter,
        "conversation": {
            "id": conversation_id,
            "type": conversation_type,
        },
        "sender": {
            "id": sender_id,
        },
        "message": {
            "type": "text",
            "text": text,
        },
    }


async def list_tasks(sessionmaker: async_sessionmaker) -> list[Task]:
    async with sessionmaker() as session:
        result = await session.scalars(select(Task).order_by(Task.created_at))
        return list(result)


async def list_users(sessionmaker: async_sessionmaker) -> list[User]:
    async with sessionmaker() as session:
        result = await session.scalars(select(User).order_by(User.created_at))
        return list(result)


async def list_processed_messages(
    sessionmaker: async_sessionmaker,
) -> list[ProcessedMessage]:
    async with sessionmaker() as session:
        result = await session.scalars(
            select(ProcessedMessage).order_by(ProcessedMessage.created_at)
        )
        return list(result)


async def create_bound_user(
    sessionmaker: async_sessionmaker,
    *,
    adapter: str = "discord",
    sender_id: str = "sender_1",
) -> str:
    async with sessionmaker() as session:
        user = User(display_name="LangBot User")
        session.add(user)
        await session.flush()
        session.add(
            PlatformAccount(
                user_id=user.id,
                platform="langbot",
                platform_user_id=f"{adapter}:{sender_id}",
            )
        )
        await session.commit()
        return user.id


async def create_user(
    sessionmaker: async_sessionmaker,
    *,
    display_name: str = "LangBot User",
) -> User:
    async with sessionmaker() as session:
        user = User(display_name=display_name)
        session.add(user)
        await session.commit()
        return user


async def create_task(
    sessionmaker: async_sessionmaker,
    *,
    user_id: str,
    task_type: str,
    input_text: str,
    platform: str = "langbot",
    status: TaskStatus = TaskStatus.PENDING,
    result_text: str | None = None,
    error_message: str | None = None,
) -> Task:
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform=platform,
            task_type=task_type,
            input_text=input_text,
            status=status.value,
            result_text=result_text,
            error_message=error_message,
        )
        session.add(task)
        await session.commit()
        return task


async def fetch_task(sessionmaker: async_sessionmaker, task_id: str) -> Task:
    async with sessionmaker() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        return task


async def fetch_tool_logs(sessionmaker: async_sessionmaker) -> list[ToolLog]:
    async with sessionmaker() as session:
        result = await session.scalars(select(ToolLog).order_by(ToolLog.created_at))
        return list(result)


class FakeLangBotClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, str]] = []

    async def send_message(
        self,
        *,
        adapter: str,
        conversation_id: str,
        conversation_type: str,
        text: str,
    ) -> dict[str, str]:
        self.calls.append(
            {
                "adapter": adapter,
                "conversation_id": conversation_id,
                "conversation_type": conversation_type,
                "text": text,
            }
        )
        if self.error is not None:
            raise self.error
        return {"message_id": "lb_sent"}


def assert_no_sensitive_text(value: str | None) -> None:
    assert value is not None
    assert "Bearer " not in value
    assert "authorization" not in value.lower()
    assert "cookie" not in value.lower()
    assert "traceback" not in value.lower()
    assert "https://private." not in value.lower()


def test_01_langbot_settings_are_placeholder_safe() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///unused.db")

    assert settings.langbot_api_base_url == "https://langbot.invalid"
    assert settings.langbot_api_key == "placeholder-langbot-api-key"
    assert settings.langbot_webhook_secret == "placeholder-langbot-webhook-secret"


@pytest.mark.asyncio
async def test_02_invalid_langbot_secret_returns_safe_error_without_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    response = client.post(
        WEBHOOK_PATH,
        json=langbot_payload("/plan 设计个人助手"),
        headers=langbot_headers(secret="wrong-secret"),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "langbot_invalid_secret"
    assert await list_tasks(sessionmaker) == []


@pytest.mark.asyncio
async def test_03_invalid_langbot_request_shape_returns_validation_error(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    response = client.post(
        WEBHOOK_PATH,
        json={"message_id": "lb_invalid_shape"},
        headers=langbot_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert await list_tasks(sessionmaker) == []


@pytest.mark.asyncio
async def test_04_valid_langbot_text_event_returns_normalized_message(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker, adapter="discord", sender_id="sender_1")

    response = client.post(
        WEBHOOK_PATH,
        json=langbot_payload(
            "/plan 设计个人助手",
            message_id="lb_normalize",
            adapter="discord",
            sender_id="sender_1",
            conversation_id="conv_project",
            conversation_type="group",
        ),
        headers=langbot_headers(),
    )

    assert response.status_code == 200
    assert response.json()["message"] == {
        "platform": "langbot",
        "adapter": "discord",
        "sender_id": "sender_1",
        "conversation_id": "conv_project",
        "conversation_type": "group",
        "text": "/plan 设计个人助手",
        "message_id": "lb_normalize",
    }


@pytest.mark.asyncio
async def test_05_bound_langbot_command_creates_pending_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    user_id = await create_bound_user(sessionmaker, adapter="discord", sender_id="sender_1")

    response = client.post(
        WEBHOOK_PATH,
        json=langbot_payload(
            "/learn LangGraph checkpoint 是什么",
            message_id="lb_learn",
        ),
        headers=langbot_headers(),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "task_created"
    tasks = await list_tasks(sessionmaker)
    assert len(tasks) == 1
    assert tasks[0].user_id == user_id
    assert tasks[0].task_type == "learn"
    assert tasks[0].input_text == "/learn LangGraph checkpoint 是什么"
    assert tasks[0].status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_06_every_mvp_command_maps_to_task_type(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)
    expected = {
        "/plan": "plan",
        "/learn": "learn",
        "/daily": "daily",
        "/office": "office",
        "/memory": "memory",
        "/status": "status",
    }

    for index, command in enumerate(expected, start=1):
        response = client.post(
            WEBHOOK_PATH,
            json=langbot_payload(
                f"{command} phase09",
                message_id=f"lb_command_{index}",
            ),
            headers=langbot_headers(),
        )
        assert response.status_code == 200
        assert response.json()["reason"] == "task_created"

    tasks = await list_tasks(sessionmaker)
    assert [task.task_type for task in tasks] == list(expected.values())


@pytest.mark.asyncio
async def test_07_unresolved_sender_acknowledged_without_task_or_user_creation(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    response = client.post(
        WEBHOOK_PATH,
        json=langbot_payload(
            "/plan 未绑定 sender",
            message_id="lb_unbound",
            sender_id="missing_sender",
        ),
        headers=langbot_headers(),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "unbound_user"
    assert await list_tasks(sessionmaker) == []
    assert await list_users(sessionmaker) == []


@pytest.mark.asyncio
async def test_08_unknown_command_acknowledged_without_executable_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)

    response = client.post(
        WEBHOOK_PATH,
        json=langbot_payload(
            "/unknown test",
            message_id="lb_unknown",
        ),
        headers=langbot_headers(),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "unknown_command"
    assert await list_tasks(sessionmaker) == []


@pytest.mark.asyncio
async def test_09_free_text_creates_pending_agent_task_without_inline_execution(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    user_id = await create_bound_user(sessionmaker)

    response = client.post(
        WEBHOOK_PATH,
        json=langbot_payload(
            "帮我规划一个不会越用越重的私人助理",
            message_id="lb_free_text_agent",
        ),
        headers=langbot_headers(),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "task_created"
    tasks = await list_tasks(sessionmaker)
    assert len(tasks) == 1
    assert tasks[0].user_id == user_id
    assert tasks[0].task_type == "agent"
    assert tasks[0].input_text == "帮我规划一个不会越用越重的私人助理"
    assert tasks[0].status == TaskStatus.PENDING.value


@pytest.mark.asyncio
async def test_09_duplicate_message_acknowledged_without_second_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)
    payload = langbot_payload("/plan 去重测试", message_id="lb_duplicate")

    first_response = client.post(
        WEBHOOK_PATH,
        json=payload,
        headers=langbot_headers(),
    )
    second_response = client.post(
        WEBHOOK_PATH,
        json=payload,
        headers=langbot_headers(),
    )

    assert first_response.status_code == 200
    assert first_response.json()["reason"] == "task_created"
    assert second_response.status_code == 200
    assert second_response.json()["reason"] == "duplicate_message"
    assert len(await list_tasks(sessionmaker)) == 1
    assert len(await list_processed_messages(sessionmaker)) == 1


@pytest.mark.asyncio
async def test_10_supported_task_triggers_only_lightweight_handoff_before_ack(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)
    handoff_calls: list[str] = []

    def record_handoff(task_id: str, **_kwargs: object) -> bool:
        handoff_calls.append(task_id)
        return True

    monkeypatch.setattr("channels.langbot.service.enqueue_task_execution", record_handoff)

    response = client.post(
        WEBHOOK_PATH,
        json=langbot_payload(
            "/daily ack 前不执行外部服务",
            message_id="lb_handoff",
        ),
        headers=langbot_headers(),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "task_created"
    tasks = await list_tasks(sessionmaker)
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.PENDING
    assert tasks[0].result_text is None
    assert tasks[0].error_message is None
    assert handoff_calls == [tasks[0].id]


@pytest.mark.asyncio
async def test_11_langbot_task_creation_records_dispatch_target(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker, adapter="discord", sender_id="sender_target")

    response = client.post(
        WEBHOOK_PATH,
        json=langbot_payload(
            "/plan 需要回推",
            message_id="lb_dispatch_target",
            adapter="discord",
            sender_id="sender_target",
            conversation_id="conv_dispatch",
            conversation_type="group",
        ),
        headers=langbot_headers(),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "task_created"
    processed_messages = await list_processed_messages(sessionmaker)
    assert len(processed_messages) == 1
    assert processed_messages[0].task_id == response.json()["task_id"]
    assert json.loads(processed_messages[0].response_target or "{}") == {
        "adapter": "discord",
        "conversation_id": "conv_dispatch",
        "conversation_type": "group",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "result_text", "error_message", "expected_text"),
    [
        (TaskStatus.SUCCESS, "任务结果", None, "已完成"),
        (TaskStatus.FAILED, None, "安全失败", "失败"),
        (TaskStatus.WAITING_APPROVAL, "需要批准 shell.exec", None, "审批"),
    ],
)
async def test_12_result_dispatcher_pushes_langbot_status_updates(
    sessionmaker: async_sessionmaker,
    status: TaskStatus,
    result_text: str | None,
    error_message: str | None,
    expected_text: str,
) -> None:
    user = await create_user(sessionmaker)
    task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="plan",
        input_text="/plan dispatch",
        status=status,
        result_text=result_text,
        error_message=error_message,
    )

    async with sessionmaker() as session:
        session.add(
            ProcessedMessage(
                platform="langbot",
                message_id=f"lb_{status.value}",
                reason="task_created",
                task_id=task.id,
                chat_id="conv_terminal",
                response_target=json.dumps(
                    {
                        "adapter": "discord",
                        "conversation_id": "conv_terminal",
                        "conversation_type": "group",
                    },
                    ensure_ascii=False,
                ),
            )
        )
        await session.commit()

    fake_langbot = FakeLangBotClient()
    async with sessionmaker() as session:
        result = await ResultDispatcher(
            session,
            langbot_client=fake_langbot,
        ).dispatch_task(task.id)

    assert result.status == "succeeded"
    assert fake_langbot.calls == [
        {
            "adapter": "discord",
            "conversation_id": "conv_terminal",
            "conversation_type": "group",
            "text": fake_langbot.calls[0]["text"],
        }
    ]
    assert expected_text in fake_langbot.calls[0]["text"]
    assert task.id in fake_langbot.calls[0]["text"]
    stored_task = await fetch_task(sessionmaker, task.id)
    assert stored_task.result_text == result_text
    assert stored_task.error_message == error_message


@pytest.mark.asyncio
async def test_13_langbot_dispatch_missing_target_fails_safely_without_send(
    sessionmaker: async_sessionmaker,
) -> None:
    user = await create_user(sessionmaker)
    task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="plan",
        input_text="/plan no target",
        status=TaskStatus.SUCCESS,
        result_text="ok",
    )

    fake_langbot = FakeLangBotClient()
    async with sessionmaker() as session:
        result = await ResultDispatcher(
            session,
            langbot_client=fake_langbot,
        ).dispatch_task(task.id)

    assert result.status == "failed"
    assert fake_langbot.calls == []
    logs = await fetch_tool_logs(sessionmaker)
    assert logs[0].status == "failed"
    assert "目标" in (logs[0].error_message or "")


@pytest.mark.asyncio
async def test_14_dispatcher_keeps_langbot_idempotency(
    sessionmaker: async_sessionmaker,
) -> None:
    user = await create_user(sessionmaker)
    langbot_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="plan",
        input_text="/plan langbot idempotent",
        platform="langbot",
        status=TaskStatus.SUCCESS,
        result_text="langbot ok",
    )
    async with sessionmaker() as session:
        session.add_all(
            [
                ProcessedMessage(
                    platform="langbot",
                    message_id="lb_dispatch_success",
                    reason="task_created",
                    task_id=langbot_task.id,
                    chat_id="conv_langbot",
                    response_target=json.dumps(
                        {
                            "adapter": "discord",
                            "conversation_id": "conv_langbot",
                            "conversation_type": "group",
                        },
                        ensure_ascii=False,
                    ),
                ),
                ToolLog(
                    task_id=langbot_task.id,
                    tool_name="langbot.result_dispatch",
                    status="succeeded",
                    input_text='{"conversation_id":"conv_langbot"}',
                    output_text='{"message_id":"lb_sent"}',
                ),
            ]
        )
        await session.commit()

    fake_langbot = FakeLangBotClient()
    async with sessionmaker() as session:
        dispatcher = ResultDispatcher(
            session,
            langbot_client=fake_langbot,
        )
        langbot_result = await dispatcher.dispatch_task(langbot_task.id)

    assert langbot_result.status == "skipped"
    assert fake_langbot.calls == []


@pytest.mark.asyncio
async def test_15_langbot_dispatch_failure_is_sanitized(
    sessionmaker: async_sessionmaker,
) -> None:
    user = await create_user(sessionmaker)
    task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="plan",
        input_text="/plan dispatch failure",
        status=TaskStatus.SUCCESS,
        result_text="业务结果仍然成功",
    )

    async with sessionmaker() as session:
        session.add(
            ProcessedMessage(
                platform="langbot",
                message_id="lb_dispatch_failure",
                reason="task_created",
                task_id=task.id,
                chat_id="conv_dispatch_failure",
                response_target=json.dumps(
                    {
                        "adapter": "discord",
                        "conversation_id": "conv_dispatch_failure",
                        "conversation_type": "group",
                    },
                    ensure_ascii=False,
                ),
            )
        )
        await session.commit()

    unsafe_error = RuntimeError(
        "Traceback Authorization: Bearer fake-token "
        "cookie=secret-token https://private.example.invalid/langbot"
    )
    fake_langbot = FakeLangBotClient(error=unsafe_error)
    async with sessionmaker() as session:
        result = await ResultDispatcher(
            session,
            langbot_client=fake_langbot,
            sensitive_values=["fake-token"],
        ).dispatch_task(task.id)

    assert result.status == "failed"
    assert_no_sensitive_text(result.message)
    stored_task = await fetch_task(sessionmaker, task.id)
    assert stored_task.status == TaskStatus.SUCCESS.value
    assert stored_task.result_text == "业务结果仍然成功"
    logs = await fetch_tool_logs(sessionmaker)
    failed_logs = [
        log for log in logs if log.task_id == task.id and log.status == "failed"
    ]
    assert failed_logs
    assert_no_sensitive_text(failed_logs[0].error_message)


def test_16_readme_documents_current_entry_surfaces() -> None:
    readme = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")

    assert "LangBot" in readme
    assert "主消息入口和结果回推通道" in readme
    assert "Electron Web 桌面端" in readme
    assert "`/local/*`" in readme
