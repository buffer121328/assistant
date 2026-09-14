from __future__ import annotations

from email.message import EmailMessage
from typing import Any

import pytest

from integrations import CalDavProvider, ProviderError, SmtpProvider


class FakeSmtpSession:
    def __init__(self) -> None:
        self.started_tls = False
        self.login_values: tuple[str, str] | None = None
        self.messages: list[EmailMessage] = []

    def __enter__(self) -> FakeSmtpSession:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def starttls(self, *, context: object) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.login_values = (user, password)

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_smtp_provider_delivers_once_with_tls_and_safe_result() -> None:
    session = FakeSmtpSession()
    provider = SmtpProvider(lambda config: session)
    provider_id = await provider.send(
        {
            "host": "smtp.example.invalid",
            "port": "587",
            "username": "owner@example.invalid",
            "password": "private-password",
            "security": "starttls",
        },
        recipients=("recipient@example.invalid",),
        subject="Subject",
        body="Body",
    )
    assert session.started_tls is True
    assert session.login_values == ("owner@example.invalid", "private-password")
    assert len(session.messages) == 1
    assert provider_id.startswith("smtp:")
    assert "private-password" not in provider_id

    check_session = FakeSmtpSession()
    await SmtpProvider(lambda config: check_session).check(
        {
            "host": "smtp.example.invalid",
            "port": "587",
            "username": "owner@example.invalid",
            "password": "private-password",
            "security": "starttls",
        }
    )
    assert check_session.started_tls is True
    assert check_session.login_values == ("owner@example.invalid", "private-password")
    assert check_session.messages == []


class FakeCalendar:
    def __init__(self) -> None:
        self.uids: set[str] = set()
        self.saved: list[str] = []
        self.checks = 0

    def search(self, *, uid: str) -> list[str]:
        return [uid] if uid in self.uids else []

    def save_event(self, event: str) -> None:
        uid = next(line[4:] for line in event.splitlines() if line.startswith("UID:"))
        self.uids.add(uid)
        self.saved.append(event)


class FakeDavClient:
    def __init__(self, calendar: FakeCalendar, **kwargs: Any) -> None:
        self._calendar = calendar

    def __enter__(self) -> FakeDavClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def principal(self) -> FakeDavClient:
        return self

    def calendar(self, url: str | None = None) -> FakeCalendar:
        return self._calendar

    def calendars(self) -> list[FakeCalendar]:
        self._calendar.checks += 1
        return [self._calendar]


@pytest.mark.asyncio
async def test_caldav_provider_uses_stable_uid_without_duplicate_event() -> None:
    calendar = FakeCalendar()
    provider = CalDavProvider(lambda **kwargs: FakeDavClient(calendar, **kwargs))
    credentials = {
        "url": "https://calendar.example.invalid",
        "username": "owner@example.invalid",
        "password": "private-password",
    }
    first = await provider.create_event(
        credentials,
        title="Meeting",
        start="20260715T090000Z",
        end="20260715T100000Z",
        description="Review",
        idempotency_key="task-1:event-1",
    )
    second = await provider.create_event(
        credentials,
        title="Meeting",
        start="20260715T090000Z",
        end="20260715T100000Z",
        description="Review",
        idempotency_key="task-1:event-1",
    )
    assert first == second
    assert len(calendar.saved) == 1
    assert "private-password" not in calendar.saved[0]
    await provider.check(credentials)
    assert calendar.checks == 1


@pytest.mark.asyncio
async def test_caldav_provider_normalizes_timezone_aware_iso_datetimes() -> None:
    calendar = FakeCalendar()
    provider = CalDavProvider(lambda **kwargs: FakeDavClient(calendar, **kwargs))
    credentials = {
        "url": "https://calendar.example.invalid",
        "username": "owner@example.invalid",
        "password": "private-password",
    }

    await provider.create_event(
        credentials,
        title="Meeting",
        start="2026-07-15T09:00:00+08:00",
        end="2026-07-15T10:00:00+08:00",
        description="Review",
        idempotency_key="task-iso:event-1",
    )

    assert "DTSTART:20260715T010000Z" in calendar.saved[0]
    assert "DTEND:20260715T020000Z" in calendar.saved[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-07-15T09:00:00", "2026-07-15T10:00:00"),
        ("2026-07-15T10:00:00Z", "2026-07-15T09:00:00Z"),
        ("not-a-date", "2026-07-15T10:00:00Z"),
    ],
)
async def test_caldav_provider_rejects_invalid_event_period(
    start: str, end: str
) -> None:
    credentials = {
        "url": "https://calendar.example.invalid",
        "username": "owner@example.invalid",
        "password": "private-password",
    }
    with pytest.raises(ProviderError, match="caldav_event_invalid"):
        await CalDavProvider().create_event(
            credentials,
            title="Meeting",
            start=start,
            end=end,
            description="Review",
            idempotency_key="task-invalid:event-1",
        )


@pytest.mark.asyncio
async def test_provider_configuration_errors_are_safe() -> None:
    with pytest.raises(ProviderError, match="smtp_config_invalid"):
        await SmtpProvider().send({}, recipients=("x@example.invalid",), subject="x", body="x")
    with pytest.raises(ProviderError, match="caldav_config_invalid"):
        await CalDavProvider().create_event(
            {"url": "http://localhost", "username": "u", "password": "secret"},
            title="x", start="x", end="x", description="x", idempotency_key="x",
        )
