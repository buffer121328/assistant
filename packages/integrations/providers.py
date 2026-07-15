from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
import smtplib
import ssl
from typing import Any, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

import caldav


class ProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class SmtpSession(Protocol):
    def __enter__(self) -> SmtpSession: ...
    def __exit__(self, *args: object) -> None: ...
    def ehlo(self) -> object: ...
    def starttls(self, *, context: ssl.SSLContext) -> object: ...
    def login(self, user: str, password: str) -> object: ...
    def send_message(self, message: EmailMessage) -> object: ...


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_address: str
    security: str = "starttls"
    timeout: float = 15.0

    @classmethod
    def from_credentials(cls, values: Mapping[str, str]) -> SmtpConfig:
        try:
            config = cls(
                host=values["host"].strip(),
                port=int(values.get("port", "587")),
                username=values["username"].strip(),
                password=values["password"],
                from_address=values.get("from_address", values["username"]).strip(),
                security=values.get("security", "starttls").lower(),
                timeout=float(values.get("timeout", "15")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("smtp_config_invalid") from exc
        if not config.host or not config.username or not config.password:
            raise ProviderError("smtp_config_invalid")
        if config.port not in range(1, 65536) or config.security not in {"starttls", "ssl"}:
            raise ProviderError("smtp_config_invalid")
        return config


class SmtpProvider:
    def __init__(self, session_factory: Callable[[SmtpConfig], SmtpSession] | None = None) -> None:
        self.session_factory = session_factory or _smtp_session

    async def send(
        self,
        credentials: Mapping[str, str],
        *,
        recipients: tuple[str, ...],
        subject: str,
        body: str,
    ) -> str:
        config = SmtpConfig.from_credentials(credentials)
        message = EmailMessage()
        message["From"] = config.from_address
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.set_content(body)
        try:
            await asyncio.to_thread(self._send, config, message)
        except smtplib.SMTPAuthenticationError as exc:
            raise ProviderError("smtp_auth_failed") from exc
        except (TimeoutError, OSError, smtplib.SMTPException) as exc:
            raise ProviderError("smtp_delivery_failed", retryable=True) from exc
        return message["Message-ID"] or f"smtp:{uuid5(NAMESPACE_URL, subject + body)}"

    async def check(self, credentials: Mapping[str, str]) -> None:
        config = SmtpConfig.from_credentials(credentials)
        try:
            await asyncio.to_thread(self._authenticate, config)
        except smtplib.SMTPAuthenticationError as exc:
            raise ProviderError("smtp_auth_failed") from exc
        except (TimeoutError, OSError, smtplib.SMTPException) as exc:
            raise ProviderError("smtp_connection_failed", retryable=True) from exc

    def _authenticate(self, config: SmtpConfig) -> None:
        with self.session_factory(config) as session:
            session.ehlo()
            if config.security == "starttls":
                session.starttls(context=ssl.create_default_context())
                session.ehlo()
            session.login(config.username, config.password)

    def _send(self, config: SmtpConfig, message: EmailMessage) -> None:
        with self.session_factory(config) as session:
            session.ehlo()
            if config.security == "starttls":
                session.starttls(context=ssl.create_default_context())
                session.ehlo()
            session.login(config.username, config.password)
            session.send_message(message)


def _smtp_session(config: SmtpConfig) -> SmtpSession:
    if config.security == "ssl":
        return cast(SmtpSession, smtplib.SMTP_SSL(config.host, config.port, timeout=config.timeout))
    return cast(SmtpSession, smtplib.SMTP(config.host, config.port, timeout=config.timeout))


class CalDavProvider:
    def __init__(self, client_factory: Callable[..., Any] | None = None) -> None:
        self.client_factory: Callable[..., Any] = client_factory or cast(
            Callable[..., Any], caldav.DAVClient
        )

    async def create_event(
        self,
        credentials: Mapping[str, str],
        *,
        title: str,
        start: str,
        end: str,
        description: str,
        idempotency_key: str,
    ) -> str:
        url, username, password, timeout = _caldav_config(credentials)
        normalized_start, normalized_end = _calendar_period(start, end)
        uid = str(uuid5(NAMESPACE_URL, idempotency_key))
        event = _icalendar(
            uid, title, normalized_start, normalized_end, description
        )
        try:
            return await asyncio.to_thread(
                self._create,
                url,
                username,
                password,
                timeout,
                credentials.get("calendar_url"),
                event,
                uid,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("caldav_delivery_failed", retryable=True) from exc

    async def check(self, credentials: Mapping[str, str]) -> None:
        url, username, password, timeout = _caldav_config(credentials)
        try:
            await asyncio.to_thread(self._check, url, username, password, timeout)
        except Exception as exc:
            raise ProviderError("caldav_connection_failed", retryable=True) from exc

    def _check(self, url: str, username: str, password: str, timeout: int) -> None:
        with self.client_factory(
            url=url, username=username, password=password, timeout=timeout
        ) as client:
            client.principal().calendars()

    def _create(
        self,
        url: str,
        username: str,
        password: str,
        timeout: int,
        calendar_url: str | None,
        event: str,
        uid: str,
    ) -> str:
        with self.client_factory(
            url=url, username=username, password=password, timeout=timeout
        ) as client:
            calendar = client.calendar(url=calendar_url) if calendar_url else client.principal().calendar()
            existing = calendar.search(uid=uid)
            if existing:
                return uid
            calendar.save_event(event)
            return uid


def _caldav_config(credentials: Mapping[str, str]) -> tuple[str, str, str, int]:
    url = credentials.get("url", "").strip()
    username = credentials.get("username", "").strip()
    password = credentials.get("password", "")
    try:
        timeout = int(credentials.get("timeout", "15"))
    except ValueError as exc:
        raise ProviderError("caldav_config_invalid") from exc
    if (
        not url.startswith("https://")
        or not username
        or not password
        or timeout not in range(1, 61)
    ):
        raise ProviderError("caldav_config_invalid")
    return url, username, password, timeout


def _calendar_period(start: str, end: str) -> tuple[str, str]:
    try:
        start_at = datetime.fromisoformat(start.strip().replace("Z", "+00:00"))
        end_at = datetime.fromisoformat(end.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderError("caldav_event_invalid") from exc
    if start_at.tzinfo is None or end_at.tzinfo is None or end_at <= start_at:
        raise ProviderError("caldav_event_invalid")
    return (
        start_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
        end_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
    )


def _icalendar(uid: str, title: str, start: str, end: str, description: str) -> str:
    def clean(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

    return "\r\n".join(
        [
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Personal Agent//V5//EN",
            "BEGIN:VEVENT", f"UID:{uid}", f"DTSTART:{start}", f"DTEND:{end}",
            f"SUMMARY:{clean(title)}", f"DESCRIPTION:{clean(description)}", "END:VEVENT",
            "END:VCALENDAR", "",
        ]
    )
