from __future__ import annotations

from collections.abc import AsyncIterator
import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from application.artifact_lifecycle import (
    ArtifactLifecycleService,
    ArtifactValidationError,
)
from application.desktop_capture.commands import parse_screen_command
from application.desktop_capture.service import ScreenTaskService
from application.task_execution.dispatch import ResultDispatcher
from domain.models import (
    ArtifactRecord,
    Base,
    PlatformAccount,
    ProcessedMessage,
    Task,
    TaskEvent,
    TaskStatus,
    ToolLog,
    User,
)
from infrastructure.settings.config import Settings


WEBHOOK_PATH = "/api/webhooks/langbot"
LANGBOT_WEBHOOK_SECRET = "test-langbot-webhook-secret"
MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"A\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/langbot_screen.db",
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
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///unused.db",
        redis_url="redis://placeholder",
        langbot_webhook_secret=LANGBOT_WEBHOOK_SECRET,
        artifacts_root=tmp_path / "artifacts",
        desktop_capture_enabled=True,
        desktop_capture_default_target="frontend",
    )


@pytest.fixture
def client(
    sessionmaker: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> TestClient:
    monkeypatch.setattr(
        "channels.langbot.service.enqueue_task_execution",
        lambda _task_id, **_kwargs: True,
    )
    app = create_app(settings)
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


def langbot_headers(*, secret: str = LANGBOT_WEBHOOK_SECRET) -> dict[str, str]:
    return {"content-type": "application/json", "x-langbot-secret": secret}


def langbot_payload(
    text: str,
    *,
    message_id: str = "lb_screen_1",
    adapter: str = "discord",
    sender_id: str = "sender_1",
    conversation_id: str = "conv_1",
    conversation_type: str = "person",
) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "adapter": adapter,
        "conversation": {"id": conversation_id, "type": conversation_type},
        "sender": {"id": sender_id},
        "message": {"type": "text", "text": text},
    }


async def create_bound_user(
    sessionmaker: async_sessionmaker,
    *,
    adapter: str = "discord",
    sender_id: str = "sender_1",
) -> str:
    async with sessionmaker() as session:
        user = User(display_name="LangBot Screen User")
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


async def list_tasks(sessionmaker: async_sessionmaker) -> list[Task]:
    async with sessionmaker() as session:
        return list(await session.scalars(select(Task).order_by(Task.created_at)))


async def list_processed_messages(
    sessionmaker: async_sessionmaker,
) -> list[ProcessedMessage]:
    async with sessionmaker() as session:
        return list(
            await session.scalars(select(ProcessedMessage).order_by(ProcessedMessage.created_at))
        )


async def list_tool_logs(sessionmaker: async_sessionmaker) -> list[ToolLog]:
    async with sessionmaker() as session:
        return list(await session.scalars(select(ToolLog).order_by(ToolLog.created_at)))


async def list_task_events(sessionmaker: async_sessionmaker) -> list[TaskEvent]:
    async with sessionmaker() as session:
        return list(
            await session.scalars(
                select(TaskEvent).order_by(TaskEvent.sequence, TaskEvent.created_at)
            )
        )


async def list_artifacts(sessionmaker: async_sessionmaker) -> list[ArtifactRecord]:
    """Return persisted screenshot Artifact records in creation order."""
    async with sessionmaker() as session:
        return list(
            await session.scalars(
                select(ArtifactRecord).order_by(ArtifactRecord.created_at)
            )
        )


class FakeScreenshotRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def capture_png(self, *, target: str) -> bytes:
        self.calls.append({"target": target})
        return MINIMAL_PNG


class FakeLangBotImageClient:
    def __init__(self, *, image_error: Exception | None = None) -> None:
        self.image_error = image_error
        self.image_calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, str]] = []

    async def send_image(
        self,
        *,
        adapter: str,
        conversation_id: str,
        conversation_type: str,
        image_reference: str,
        mime_type: str,
        text: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        del idempotency_key
        self.image_calls.append(
            {
                "adapter": adapter,
                "conversation_id": conversation_id,
                "conversation_type": conversation_type,
                "image_reference": image_reference,
                "mime_type": mime_type,
                "text": text,
            }
        )
        if self.image_error is not None:
            raise self.image_error
        return {"message_id": "lb_image_sent"}

    async def send_message(
        self,
        *,
        adapter: str,
        conversation_id: str,
        conversation_type: str,
        text: str,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        del idempotency_key
        self.text_calls.append(
            {
                "adapter": adapter,
                "conversation_id": conversation_id,
                "conversation_type": conversation_type,
                "text": text,
            }
        )
        return {"message_id": "lb_text_sent"}


def test_ac_command_parser_accepts_only_explicit_screen_commands() -> None:
    default_command = parse_screen_command("/screen")
    desktop_command = parse_screen_command("/screen desktop")
    frontend_command = parse_screen_command("/screen frontend")

    assert default_command is not None
    assert desktop_command is not None
    assert frontend_command is not None
    assert default_command.target == "frontend"
    assert desktop_command.target == "desktop"
    assert frontend_command.target == "frontend"
    assert parse_screen_command("/plan screenshot") is None

    with pytest.raises(ValueError, match="unsupported_screen_target"):
        parse_screen_command("/screen browser")


@pytest.mark.asyncio
async def test_ac01_bound_sender_screen_command_creates_screen_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    user_id = await create_bound_user(sessionmaker)

    response = client.post(
        WEBHOOK_PATH,
        json=langbot_payload(
            "/screen",
            message_id="lb_screen_create",
            adapter="discord",
            sender_id="sender_1",
            conversation_id="conv_phone",
            conversation_type="person",
        ),
        headers=langbot_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reason"] == "task_created"
    assert body["task_type"] == "screen"
    tasks = await list_tasks(sessionmaker)
    assert len(tasks) == 1
    assert tasks[0].user_id == user_id
    assert tasks[0].platform == "langbot"
    assert tasks[0].task_type == "screen"
    assert tasks[0].input_text == "/screen"
    ledger = (await list_processed_messages(sessionmaker))[0]
    assert ledger.task_id == tasks[0].id
    assert json.loads(ledger.response_target or "{}") == {
        "adapter": "discord",
        "conversation_id": "conv_phone",
        "conversation_type": "person",
    }


@pytest.mark.asyncio
async def test_ac02_duplicate_message_does_not_create_second_screenshot_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)
    payload = langbot_payload("/screen", message_id="lb_screen_duplicate")

    first = client.post(WEBHOOK_PATH, json=payload, headers=langbot_headers())
    second = client.post(WEBHOOK_PATH, json=payload, headers=langbot_headers())

    assert first.status_code == 200
    assert first.json()["reason"] == "task_created"
    assert second.status_code == 200
    assert second.json()["reason"] == "duplicate_message"
    assert len(await list_tasks(sessionmaker)) == 1
    assert len(await list_processed_messages(sessionmaker)) == 1


@pytest.mark.asyncio
async def test_ac03_unbound_sender_screen_command_is_safely_rejected(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    response = client.post(
        WEBHOOK_PATH,
        json=langbot_payload("/screen", message_id="lb_screen_unbound", sender_id="missing"),
        headers=langbot_headers(),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "unbound_user"
    assert await list_tasks(sessionmaker) == []


@pytest.mark.asyncio
async def test_ac04_screen_task_uses_governed_tool_and_writes_artifact(
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    user_id = await create_bound_user(sessionmaker)
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="langbot",
            task_type="screen",
            input_text="/screen frontend",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    runner = FakeScreenshotRunner()
    async with sessionmaker() as session:
        result = await ScreenTaskService(
            session,
            settings=settings,
            screenshot_runner=runner,
        ).execute_task(task_id)

    assert result.status == TaskStatus.SUCCESS.value
    assert "截图已生成" in (result.result_text or "")
    assert runner.calls == [{"target": "frontend"}]
    logs = await list_tool_logs(sessionmaker)
    screenshot_logs = [log for log in logs if log.tool_name == "desktop.screenshot"]
    assert len(screenshot_logs) == 1
    assert screenshot_logs[0].status == "succeeded"
    output = json.loads(screenshot_logs[0].output_text or "{}")
    assert output["artifact_id"]
    assert output["filename"] == "screenshot.png"
    artifact_path = settings.artifacts_root / output["path"]
    assert artifact_path.read_bytes() == MINIMAL_PNG
    artifacts = await list_artifacts(sessionmaker)
    assert len(artifacts) == 1
    assert artifacts[0].id == output["artifact_id"]
    assert artifacts[0].task_id == task_id
    assert artifacts[0].content_hash
    assert artifacts[0].publication_state == "unpublished"
    events = await list_task_events(sessionmaker)
    assert [event.event_type for event in events] == [
        "screen.request.received",
        "screen.capture.started",
        "screen.capture.succeeded",
    ]


@pytest.mark.asyncio
async def test_ac05_langbot_dispatch_sends_image_before_text(
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    user_id = await create_bound_user(sessionmaker)
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="langbot",
            task_type="screen",
            input_text="/screen frontend",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.flush()
        session.add(
            ProcessedMessage(
                platform="langbot",
                adapter="discord",
                message_id="lb_screen_dispatch_image",
                reason="task_created",
                task_id=task.id,
                chat_id="conv_phone",
                response_target=json.dumps(
                    {
                        "adapter": "discord",
                        "conversation_id": "conv_phone",
                        "conversation_type": "person",
                    },
                    ensure_ascii=False,
                ),
                delivery_status="pending",
            )
        )
        await session.commit()
        task_id = task.id

    async with sessionmaker() as session:
        await ScreenTaskService(
            session,
            settings=settings,
            screenshot_runner=FakeScreenshotRunner(),
        ).execute_task(task_id)

    fake = FakeLangBotImageClient()
    async with sessionmaker() as session:
        result = await ResultDispatcher(session, langbot_client=fake).dispatch_task(task_id)

    assert result.status == "succeeded"
    assert len(fake.image_calls) == 1
    assert fake.image_calls[0]["conversation_id"] == "conv_phone"
    assert fake.image_calls[0]["mime_type"] == "image/png"
    assert fake.text_calls == []
    ledger = (await list_processed_messages(sessionmaker))[0]
    assert ledger.delivery_status == "succeeded"


@pytest.mark.asyncio
async def test_ac05_langbot_dispatch_falls_back_to_text_when_image_fails(
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    user_id = await create_bound_user(sessionmaker)
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="langbot",
            task_type="screen",
            input_text="/screen frontend",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.flush()
        session.add(
            ProcessedMessage(
                platform="langbot",
                adapter="discord",
                message_id="lb_screen_dispatch_fallback",
                reason="task_created",
                task_id=task.id,
                chat_id="conv_phone",
                response_target=json.dumps(
                    {
                        "adapter": "discord",
                        "conversation_id": "conv_phone",
                        "conversation_type": "person",
                    },
                    ensure_ascii=False,
                ),
                delivery_status="pending",
            )
        )
        await session.commit()
        task_id = task.id

    async with sessionmaker() as session:
        await ScreenTaskService(
            session,
            settings=settings,
            screenshot_runner=FakeScreenshotRunner(),
        ).execute_task(task_id)

    fake = FakeLangBotImageClient(image_error=RuntimeError("image unsupported"))
    async with sessionmaker() as session:
        result = await ResultDispatcher(session, langbot_client=fake).dispatch_task(task_id)

    assert result.status == "succeeded"
    assert len(fake.image_calls) == 1
    assert len(fake.text_calls) == 1
    assert "截图已生成，但当前 LangBot 适配器未成功发送图片" in fake.text_calls[0]["text"]
    assert "任务ID" in fake.text_calls[0]["text"]
    logs = await list_tool_logs(sessionmaker)
    dispatch_logs = [log for log in logs if log.tool_name == "langbot.result_dispatch"]
    assert dispatch_logs[-1].status == "succeeded"
    output = json.loads(dispatch_logs[-1].output_text or "{}")
    assert output["fallback_reason"] == "image_send_failed"


@pytest.mark.asyncio
async def test_ac06_desktop_screenshot_source_unavailable_is_rejected_and_logged(
    sessionmaker: async_sessionmaker,
    settings: Settings,
) -> None:
    disabled_settings = settings.model_copy(update={"desktop_capture_enabled": False})
    user_id = await create_bound_user(sessionmaker)
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="langbot",
            task_type="screen",
            input_text="/screen desktop",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async with sessionmaker() as session:
        result = await ScreenTaskService(
            session,
            settings=disabled_settings,
            screenshot_runner=FakeScreenshotRunner(),
        ).execute_task(task_id)

    assert result.status == TaskStatus.FAILED.value
    assert result.error_message == "desktop_capture_unavailable"
    logs = await list_tool_logs(sessionmaker)
    failed = [log for log in logs if log.tool_name == "desktop.screenshot"]
    assert failed[-1].status == "failed"
    assert "Tool source is unavailable" in (failed[-1].error_message or "")


@pytest.mark.asyncio
async def test_ac07_screenshot_registration_failure_never_reports_artifact_success(
    sessionmaker: async_sessionmaker,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persistence failure follows the bounded tool failure path without an ID."""
    user_id = await create_bound_user(sessionmaker)
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="langbot",
            task_type="screen",
            input_text="/screen frontend",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def fail_registration(
        _service: ArtifactLifecycleService,
        **_kwargs: object,
    ) -> object:
        """Simulate a bounded registration failure after valid capture bytes."""
        raise ArtifactValidationError("Artifact registration unavailable")

    monkeypatch.setattr(ArtifactLifecycleService, "register_bytes", fail_registration)
    async with sessionmaker() as session:
        result = await ScreenTaskService(
            session,
            settings=settings,
            screenshot_runner=FakeScreenshotRunner(),
        ).execute_task(task_id)

    assert result.status == TaskStatus.FAILED.value
    assert await list_artifacts(sessionmaker) == []
    events = await list_task_events(sessionmaker)
    assert "screen.capture.succeeded" not in [event.event_type for event in events]
