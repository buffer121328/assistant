from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domain.models import (
    Base,
    DeliveryAttempt,
    NotificationOutbox,
    ProcessedMessage,
    Task,
    User,
)
from infrastructure.settings.config import Settings
from channels.langbot.service import LangBotResultClient
from app.main import create_app
from integrations.notifications import (
    NotificationError,
    ReminderService,
    deliver_langbot_due,
)


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/notifications.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def add_user(sessionmaker: async_sessionmaker[AsyncSession], name: str) -> str:
    async with sessionmaker() as session:
        user = User(display_name=name)
        session.add(user)
        await session.commit()
        return user.id


class FakeLangBotClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def send_message(self, **kwargs: str) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"ok": True}


class AmbiguousLangBotClient:
    def __init__(self) -> None:
        self.calls = 0
        self.delivered_keys: set[str] = set()

    async def send_message(self, **kwargs: str) -> dict[str, Any]:
        self.calls += 1
        self.delivered_keys.add(kwargs["idempotency_key"])
        if self.calls == 1:
            raise RuntimeError("response lost after remote acceptance")
        return {"ok": True}


class BlockingLangBotClient:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send_message(self, **kwargs: str) -> dict[str, Any]:
        del kwargs
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return {"ok": True}


@pytest.mark.asyncio
async def test_desktop_outbox_is_unique_owned_acknowledged_and_cancellable(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await add_user(sessionmaker, "Owner")
    other_id = await add_user(sessionmaker, "Other")
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    async with sessionmaker() as session:
        service = ReminderService(session)
        reminder = await service.create(
            user_id=owner_id,
            title="Stand up",
            message="Move for five minutes",
            due_at=now - timedelta(minutes=1),
            channel="desktop",
        )
        first = await service.materialize_due(now=now)
        second = await ReminderService(session).materialize_due(now=now)
        assert len(first) == 1
        assert second == ()
        assert await service.poll_desktop(user_id=other_id, now=now) == ()
        notifications = await service.poll_desktop(user_id=owner_id, now=now)
        assert notifications[0].title == "Stand up"
        await service.acknowledge_desktop(
            user_id=owner_id, outbox_id=notifications[0].outbox_id
        )
        await service.acknowledge_desktop(
            user_id=owner_id, outbox_id=notifications[0].outbox_id
        )
        assert await service.poll_desktop(user_id=owner_id, now=now) == ()

        future = await service.create(
            user_id=owner_id,
            title="Later",
            message="Do not deliver",
            due_at=now + timedelta(hours=1),
            channel="desktop",
        )
        with pytest.raises(NotificationError, match="reminder_not_found"):
            await service.cancel(user_id=other_id, reminder_id=future.id)
        cancelled = await service.cancel(user_id=owner_id, reminder_id=future.id)
        assert cancelled.status == "cancelled"
        await service.materialize_due(now=now + timedelta(hours=2))

        outbox_count = await session.scalar(select(func.count(NotificationOutbox.id)))
        attempt_count = await session.scalar(select(func.count(DeliveryAttempt.id)))
        await session.refresh(reminder)
    assert outbox_count == 1
    assert attempt_count == 1
    assert reminder.status == "completed"


@pytest.mark.asyncio
async def test_langbot_delivery_requires_owned_target_retries_and_never_redelivers(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    missing_target_user = await add_user(sessionmaker, "Missing")
    owner_id = await add_user(sessionmaker, "Owner")
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    client = FakeLangBotClient()
    async with sessionmaker() as session:
        service = ReminderService(session)
        await service.create(
            user_id=missing_target_user,
            title="No target",
            message="Must not send",
            due_at=now,
            channel="langbot",
        )
        await service.materialize_due(now=now)
        assert await deliver_langbot_due(session=session, client=client, now=now) == ()
        assert client.calls == []
        missing = await session.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.user_id == missing_target_user
            )
        )
        assert missing is not None
        assert missing.status == "retry"
        assert missing.last_error_code == "langbot_target_missing"

        task = Task(
            user_id=owner_id,
            platform="langbot",
            task_type="agent",
            input_text="remember this target",
            status="success",
        )
        session.add(task)
        await session.flush()
        session.add(
            ProcessedMessage(
                platform="langbot",
                message_id="message-1",
                reason="task_created",
                task_id=task.id,
                response_target=json.dumps(
                    {
                        "adapter": "safe-adapter",
                        "conversation_id": "conversation-1",
                        "conversation_type": "private",
                    }
                ),
            )
        )
        await session.commit()
        reminder = await service.create(
            user_id=owner_id,
            title="Review",
            message="Open the report",
            due_at=now,
            channel="langbot",
        )
        created = await service.materialize_due(now=now)
        delivered = await deliver_langbot_due(session=session, client=client, now=now)
        repeated = await deliver_langbot_due(
            session=session, client=client, now=now + timedelta(hours=1)
        )
        await session.refresh(reminder)
    assert len(created) == 1
    assert len(delivered) == 1
    assert repeated == ()
    assert len(client.calls) == 1
    assert client.calls[0]["conversation_id"] == "conversation-1"
    assert reminder.status == "completed"


@pytest.mark.asyncio
async def test_concurrent_langbot_workers_claim_one_outbox_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await add_user(sessionmaker, "Owner")
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    client = BlockingLangBotClient()
    async with sessionmaker() as setup_session:
        task = Task(
            user_id=owner_id,
            platform="langbot",
            task_type="agent",
            input_text="remember this target",
            status="success",
        )
        setup_session.add(task)
        await setup_session.flush()
        setup_session.add(
            ProcessedMessage(
                platform="langbot",
                message_id="message-concurrent",
                reason="task_created",
                task_id=task.id,
                response_target=json.dumps(
                    {
                        "adapter": "safe-adapter",
                        "conversation_id": "conversation-1",
                        "conversation_type": "private",
                    }
                ),
            )
        )
        await setup_session.commit()
        await ReminderService(setup_session).create(
            user_id=owner_id,
            title="Review",
            message="Open the report",
            due_at=now,
            channel="langbot",
        )
        await ReminderService(setup_session).materialize_due(now=now)

    async with sessionmaker() as first_session, sessionmaker() as second_session:
        first_delivery = asyncio.create_task(
            deliver_langbot_due(session=first_session, client=client, now=now)
        )
        await client.started.wait()
        second_result = await deliver_langbot_due(
            session=second_session, client=client, now=now
        )
        client.release.set()
        first_result = await first_delivery

    assert len(first_result) == 1
    assert second_result == ()
    assert client.calls == 1


@pytest.mark.asyncio
async def test_langbot_retry_reuses_stable_idempotency_key_after_ambiguous_result(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await add_user(sessionmaker, "Owner")
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    client = AmbiguousLangBotClient()
    async with sessionmaker() as session:
        task = Task(
            user_id=owner_id,
            platform="langbot",
            task_type="agent",
            input_text="remember this target",
            status="success",
        )
        session.add(task)
        await session.flush()
        session.add(
            ProcessedMessage(
                platform="langbot",
                message_id="message-ambiguous",
                reason="task_created",
                task_id=task.id,
                response_target=json.dumps(
                    {
                        "adapter": "safe-adapter",
                        "conversation_id": "conversation-1",
                        "conversation_type": "private",
                    }
                ),
            )
        )
        await session.commit()
        reminder = await ReminderService(session).create(
            user_id=owner_id,
            title="Review",
            message="Open the report",
            due_at=now,
            channel="langbot",
        )
        await ReminderService(session).materialize_due(now=now)
        assert await deliver_langbot_due(session=session, client=client, now=now) == ()
        delivered = await deliver_langbot_due(
            session=session, client=client, now=now + timedelta(minutes=2)
        )
        outbox = await session.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.reminder_id == reminder.id
            )
        )

    assert len(delivered) == 1
    assert client.calls == 2
    assert len(client.delivered_keys) == 1
    assert outbox is not None
    assert outbox.status == "delivered"


@pytest.mark.asyncio
async def test_langbot_http_adapter_forwards_idempotency_key(
    respx_mock: Any,
) -> None:
    route = respx_mock.post("https://langbot.example.invalid/send").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = LangBotResultClient(
        Settings(
            langbot_api_base_url="https://langbot.example.invalid/send",
            langbot_api_key="test-key",
        )
    )
    result = await client.send_message(
        adapter="safe-adapter",
        conversation_id="conversation-1",
        conversation_type="private",
        text="Reminder",
        idempotency_key="reminder:stable-key",
    )

    request = route.calls[0].request
    payload = json.loads(request.content)
    assert result == {"ok": True}
    assert request.headers["Idempotency-Key"] == "reminder:stable-key"
    assert payload["idempotency_key"] == "reminder:stable-key"


@pytest.mark.asyncio
async def test_reminder_and_desktop_notification_api_enforce_ownership(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await add_user(sessionmaker, "Owner")
    other_id = await add_user(sessionmaker, "Other")
    now = datetime.now(UTC)
    app = create_app()
    app.state.db_sessionmaker = sessionmaker
    with TestClient(app) as client:
        created = client.post(
            "/api/reminders",
            json={
                "user_id": owner_id,
                "title": "API reminder",
                "message": "Show a desktop notification",
                "due_at": (now - timedelta(seconds=1)).isoformat(),
                "channel": "desktop",
            },
        )
        hidden = client.get("/api/reminders", params={"user_id": other_id})
        visible = client.get("/api/reminders", params={"user_id": owner_id})
        assert created.status_code == 201
        assert hidden.json()["items"] == []
        assert visible.json()["items"][0]["status"] == "pending"

    async with sessionmaker() as session:
        await ReminderService(session).materialize_due(now=now)

    with TestClient(app) as client:
        polled = client.get("/api/notifications/poll", params={"user_id": owner_id})
        outbox_id = polled.json()["items"][0]["outbox_id"]
        denied = client.post(
            f"/api/notifications/{outbox_id}/ack", json={"user_id": other_id}
        )
        acknowledged = client.post(
            f"/api/notifications/{outbox_id}/ack", json={"user_id": owner_id}
        )
        empty = client.get("/api/notifications/poll", params={"user_id": owner_id})
        completed = client.get("/api/reminders", params={"user_id": owner_id})
    assert denied.status_code == 404
    assert acknowledged.status_code == 204
    assert empty.json()["items"] == []
    assert completed.json()["items"][0]["status"] == "completed"


def test_desktop_reminder_client_dialog_and_notification_ack_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pytest.importorskip("PySide6.QtCore")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from assistant_desktop.client import DesktopApiClient
    from assistant_desktop.reminder_dialog import ReminderManagerDialog
    from assistant_desktop.window import TaskWindow

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"items": []})
        if request.url.path.endswith("/ack"):
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "reminder_id": "reminder-1",
                "user_id": "user-1",
                "title": "Review",
                "message": "Open report",
                "due_at": "2026-07-15T09:00:00Z",
                "channel": "desktop",
                "status": "pending",
                "cancelled_at": None,
            },
        )

    client = DesktopApiClient(
        base_url="http://127.0.0.1:8000",
        user_id="user-1",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.create_reminder(
            title="Review",
            message="Open report",
            due_at="2026-07-15T09:00:00+08:00",
            channel="desktop",
        )
        client.list_reminders()
        client.cancel_reminder("reminder-1")
        client.poll_notifications()
        client.acknowledge_notification("outbox-1")
    finally:
        client.close()
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/api/reminders"),
        ("GET", "/api/reminders"),
        ("POST", "/api/reminders/reminder-1/cancel"),
        ("GET", "/api/notifications/poll"),
        ("POST", "/api/notifications/outbox-1/ack"),
    ]

    application = QApplication.instance() or QApplication([])
    dialog = ReminderManagerDialog(base_url="http://127.0.0.1:8000", user_id="user-1")
    dialog._reminders_refreshed(  # noqa: SLF001 - verify visible reminder state
        [
            {
                "reminder_id": "reminder-1",
                "title": "Review",
                "due_at": "2026-07-15T09:00:00Z",
                "channel": "desktop",
                "status": "retry",
            }
        ]
    )
    assert dialog.reminder_list.item(0).text().startswith("[retry] Review")

    settings = QSettings(
        str(tmp_path / "notifications.ini"), QSettings.Format.IniFormat
    )
    settings.setValue("api_base_url", "http://127.0.0.1:8000")
    settings.setValue("user_id", "user-1")
    window = TaskWindow(settings=settings)
    operations: list[str] = []

    def capture_request(key: str, operation: object, on_success: object) -> None:
        del operation, on_success
        operations.append(key)

    monkeypatch.setattr(window, "_start_request", capture_request)
    window._notifications_polled(  # noqa: SLF001 - verify ack scheduling
        [
            {
                "outbox_id": "outbox-1",
                "title": "Review",
                "message": "Open report",
            }
        ]
    )
    assert operations == ["notification-ack:outbox-1"]
    dialog.close()
    window.shutdown()
    application.processEvents()
