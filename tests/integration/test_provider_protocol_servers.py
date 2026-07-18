from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import ssl
from typing import Any, cast

import caldav
import pytest

from integrations import CalDavProvider, SmtpProvider
from tests.integration.protocol_servers import caldav_server, smtp_server


@pytest.mark.asyncio
async def test_smtp_provider_against_local_starttls_auth_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with smtp_server(tmp_path) as (port, certificate, sink):
        create_context = ssl.create_default_context
        monkeypatch.setattr(
            "integrations.providers.ssl.create_default_context",
            lambda: create_context(cafile=str(certificate)),
        )
        provider = SmtpProvider()
        await provider.check(
            {
                "host": "127.0.0.1",
                "port": str(port),
                "username": "owner",
                "password": "password",
                "from_address": "owner@example.invalid",
                "security": "starttls",
            }
        )
        result = await provider.send(
            {
                "host": "127.0.0.1",
                "port": str(port),
                "username": "owner",
                "password": "password",
                "from_address": "owner@example.invalid",
                "security": "starttls",
            },
            recipients=("recipient@example.invalid",),
            subject="Local protocol smoke",
            body="One controlled message",
        )
    assert result.startswith("smtp:")
    assert len(sink.messages) == 1
    assert b"One controlled message" in sink.messages[0]


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:Unverified HTTPS request")
async def test_caldav_provider_against_local_https_server(tmp_path: Path) -> None:
    with caldav_server(tmp_path) as url:
        dav_client = cast(Callable[..., Any], caldav.DAVClient)

        def client_factory(**kwargs: Any) -> Any:
            return dav_client(**kwargs, ssl_verify_cert=False)

        with client_factory(url=url, username="owner", password="password") as client:
            client.principal().make_calendar(name="Agent protocol test")

        provider = CalDavProvider(client_factory)
        credentials = {"url": url, "username": "owner", "password": "password"}
        await provider.check(credentials)
        first = await provider.create_event(
            credentials,
            title="Protocol event",
            start="20260715T090000Z",
            end="20260715T093000Z",
            description="Controlled local event",
            idempotency_key="local-caldav-protocol-v1",
        )
        second = await provider.create_event(
            credentials,
            title="Protocol event",
            start="20260715T090000Z",
            end="20260715T093000Z",
            description="Controlled local event",
            idempotency_key="local-caldav-protocol-v1",
        )
    assert first == second
