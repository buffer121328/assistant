from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from infrastructure.config import Settings
from app.main import create_app
from domain.models import (
    AccountConnection,
    Approval,
    Base,
    ConnectionAuditLog,
    Task,
    ToolLog,
    User,
)
from integrations import (
    AccountBackedProviders,
    CredentialCipher,
    DefaultConnectionTester,
    ProviderError,
    SmtpProvider,
)
from tools import (
    ToolApprovalRequiredError,
    ToolInvocation,
    ToolRegistry,
    ToolSpec,
    external_approval_binding,
)
from tools.builtin.personal import build_personal_tool_descriptors

MASTER_KEY = "test-account-master-key-with-more-than-32-chars"


class PassingConnectionTester:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    async def test(self, provider: str, credentials: Mapping[str, str]) -> None:
        self.calls.append((provider, credentials))


class FailingConnectionTester:
    async def test(self, provider: str, credentials: Mapping[str, str]) -> None:
        del provider, credentials
        raise ProviderError("smtp_auth_failed")


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/connections.db", poolclass=NullPool
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


def client_for(
    sessionmaker: async_sessionmaker[AsyncSession], *, with_key: bool = True
) -> TestClient:
    settings = Settings(
        credential_master_key=SecretStr(MASTER_KEY if with_key else "")
    )
    app = create_app(settings)
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


@pytest.mark.asyncio
async def test_connection_credentials_are_encrypted_owned_and_revocable(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await add_user(sessionmaker, "Owner")
    other_id = await add_user(sessionmaker, "Other")
    secret = "private-app-password"
    tester = PassingConnectionTester()

    with client_for(sessionmaker) as client:
        cast(Any, client.app).state.connection_tester = tester
        created = client.post(
            "/api/connections",
            json={
                "user_id": owner_id,
                "provider": "smtp",
                "display_name": "Mail",
                "credentials": {
                    "host": "smtp.example.invalid",
                    "username": "owner@example.invalid",
                    "password": secret,
                },
            },
        )
        connection_id = created.json()["connection_id"]
        visible = client.get("/api/connections", params={"user_id": owner_id})
        hidden = client.get("/api/connections", params={"user_id": other_id})
        denied = client.post(
            f"/api/connections/{connection_id}/disable",
            json={"user_id": other_id},
        )
        untested = client.post(
            f"/api/connections/{connection_id}/test",
            json={"user_id": owner_id},
        )
        revoked = client.delete(
            f"/api/connections/{connection_id}", params={"user_id": owner_id}
        )

    assert created.status_code == 201
    assert secret not in created.text
    assert visible.json()["items"][0]["status"] == "active"
    assert secret not in visible.text
    assert hidden.json()["items"] == []
    assert denied.status_code == 404
    assert untested.status_code == 200
    assert tester.calls[0][0] == "smtp"
    assert tester.calls[0][1]["password"] == secret
    assert revoked.json()["status"] == "revoked"

    async with sessionmaker() as session:
        stored = await session.get(AccountConnection, connection_id)
        audits = list(await session.scalars(select(ConnectionAuditLog)))
    assert stored is not None
    assert secret not in stored.credential_ciphertext
    assert "owner@example.invalid" not in stored.credential_ciphertext
    assert stored.status == "revoked"
    assert {audit.action for audit in audits} == {"create", "test", "revoke"}


def test_application_installs_real_connection_tester_by_default() -> None:
    app = create_app(Settings(credential_master_key=SecretStr(MASTER_KEY)))
    assert isinstance(app.state.connection_tester, DefaultConnectionTester)


@pytest.mark.asyncio
async def test_connection_test_persists_only_safe_provider_error(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await add_user(sessionmaker, "Owner")
    secret = "private-app-password"
    with client_for(sessionmaker) as client:
        cast(Any, client.app).state.connection_tester = FailingConnectionTester()
        created = client.post(
            "/api/connections",
            json={
                "user_id": user_id,
                "provider": "smtp",
                "display_name": "Mail",
                "credentials": {
                    "host": "smtp.example.invalid",
                    "username": "owner@example.invalid",
                    "password": secret,
                },
            },
        )
        connection_id = created.json()["connection_id"]
        response = client.post(
            f"/api/connections/{connection_id}/test",
            json={"user_id": user_id},
        )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "smtp_auth_failed"
    assert secret not in response.text
    async with sessionmaker() as session:
        stored = await session.get(AccountConnection, connection_id)
        audit = await session.scalar(
            select(ConnectionAuditLog).where(ConnectionAuditLog.action == "test")
        )
    assert stored is not None
    assert stored.last_error_code == "smtp_auth_failed"
    assert audit is not None
    assert audit.error_code == "smtp_auth_failed"


@pytest.mark.asyncio
async def test_missing_master_key_fails_closed_without_database_write(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await add_user(sessionmaker, "Owner")
    with client_for(sessionmaker, with_key=False) as client:
        response = client.post(
            "/api/connections",
            json={
                "user_id": user_id,
                "provider": "smtp",
                "display_name": "Mail",
                "credentials": {"password": "must-not-persist"},
            },
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "credential_master_key_unavailable"
    assert "must-not-persist" not in response.text
    async with sessionmaker() as session:
        assert await session.scalar(select(AccountConnection)) is None


@pytest.mark.asyncio
async def test_external_action_approval_is_bound_to_exact_arguments_and_safe_audit(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await add_user(sessionmaker, "Owner")
    calls: list[dict[str, object]] = []
    arguments = {
        "connection_id": "connection-1",
        "to": ["recipient@example.invalid"],
        "subject": "Subject",
        "body": "private message body",
    }
    binding = external_approval_binding("email.send", arguments)

    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="desktop",
            task_type="agent",
            input_text="send email",
            status="running",
        )
        session.add(task)
        await session.flush()
        session.add(
            Approval(
                task_id=task.id,
                tool_name="email.send",
                approval_type="tool",
                subject=binding.subject,
                request_summary=binding.summary,
                status="approved",
                decided_by_user_id=user_id,
            )
        )
        await session.flush()

        async def handler(invocation: ToolInvocation) -> dict[str, bool]:
            calls.append(invocation.arguments)
            return {"sent": True}

        registry = ToolRegistry(session=session)
        registry.register(
            ToolSpec(
                name="email.send",
                description="Send email",
                risk_level="L3",
                handler=handler,
            )
        )
        result = await registry.execute(
            ToolInvocation(
                task_id=task.id,
                user_id=user_id,
                name="email.send",
                arguments=arguments,
            ),
            allowed_tools=(),
            approval_required_tools=("email.send",),
        )
        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=user_id,
                    name="email.send",
                    arguments={**arguments, "body": "changed body"},
                ),
                allowed_tools=(),
                approval_required_tools=("email.send",),
            )
        logs = list(await session.scalars(select(ToolLog).order_by(ToolLog.created_at)))

    assert result == {"sent": True}
    assert calls == [arguments]
    assert all("private message body" not in (log.input_text or "") for log in logs)
    assert binding.fingerprint in (logs[0].input_text or "")


class RecordingSmtpProvider(SmtpProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[Mapping[str, str], tuple[str, ...]]] = []

    async def send(
        self,
        credentials: Mapping[str, str],
        *,
        recipients: tuple[str, ...],
        subject: str,
        body: str,
    ) -> str:
        self.calls.append((credentials, recipients))
        return "smtp:safe-id"


@pytest.mark.asyncio
async def test_account_backed_provider_requires_active_owned_connection(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await add_user(sessionmaker, "Owner")
    other_id = await add_user(sessionmaker, "Other")
    cipher = CredentialCipher(MASTER_KEY)
    smtp = RecordingSmtpProvider()

    async with sessionmaker() as session:
        active = AccountConnection(
            user_id=owner_id,
            provider="smtp",
            display_name="Mail",
            credential_ciphertext=cipher.encrypt(
                {
                    "host": "smtp.example.invalid",
                    "username": "owner@example.invalid",
                    "password": "private-password",
                }
            ),
            credential_version="fernet-v1",
            status="active",
        )
        disabled = AccountConnection(
            user_id=owner_id,
            provider="smtp",
            display_name="Disabled",
            credential_ciphertext=cipher.encrypt({"password": "disabled-secret"}),
            credential_version="fernet-v1",
            status="disabled",
        )
        session.add_all((active, disabled))
        await session.flush()
        providers = AccountBackedProviders(session, cipher=cipher, smtp=smtp)

        provider_id = await providers.send(
            user_id=owner_id,
            connection_id=active.id,
            recipients=("recipient@example.invalid",),
            subject="Subject",
            body="Body",
        )
        with pytest.raises(ProviderError, match="connection_unavailable"):
            await providers.send(
                user_id=other_id,
                connection_id=active.id,
                recipients=("recipient@example.invalid",),
                subject="Subject",
                body="Body",
            )
        with pytest.raises(ProviderError, match="connection_unavailable"):
            await providers.send(
                user_id=owner_id,
                connection_id=disabled.id,
                recipients=("recipient@example.invalid",),
                subject="Subject",
                body="Body",
            )

    assert provider_id == "smtp:safe-id"
    assert len(smtp.calls) == 1
    assert smtp.calls[0][0]["password"] == "private-password"

    descriptors = build_personal_tool_descriptors(
        browser_available=False,
        sandbox_available=False,
        email_provider_available=True,
        calendar_provider_available=True,
    )
    by_name = {item.name: item for item in descriptors}
    assert "connection_id" in by_name["email.send"].input_schema["required"]
    assert "connection_id" in by_name["calendar.sync_event"].input_schema["required"]


def test_desktop_account_client_and_dialog_never_rehydrate_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from assistant_desktop.account_dialog import AccountManagerDialog
    from assistant_desktop.client import DesktopApiClient

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "connection_id": "connection-1",
                            "provider": "smtp",
                            "display_name": "Mail",
                            "status": "active",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "connection_id": "connection-1",
                "provider": "smtp",
                "display_name": "Mail",
                "status": "active",
            },
        )

    client = DesktopApiClient(
        base_url="http://127.0.0.1:8000",
        user_id="user-1",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.list_connections()[0]["connection_id"] == "connection-1"
        client.create_connection(
            provider="smtp",
            display_name="Mail",
            credentials={"host": "smtp.example.invalid", "password": "test-secret"},
        )
        client.test_connection("connection-1")
        client.disable_connection("connection-1")
        client.revoke_connection("connection-1")
    finally:
        client.close()
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/connections"),
        ("POST", "/api/connections"),
        ("POST", "/api/connections/connection-1/test"),
        ("POST", "/api/connections/connection-1/disable"),
        ("DELETE", "/api/connections/connection-1"),
    ]

    application = QApplication.instance() or QApplication([])
    dialog = AccountManagerDialog(
        base_url="http://127.0.0.1:8000",
        user_id="user-1",
    )
    assert {
        dialog.provider.itemData(index) for index in range(dialog.provider.count())
    } == {"smtp", "caldav", "browser"}
    dialog.provider.setCurrentIndex(dialog.provider.findData("browser"))
    assert dialog.password.isHidden()
    dialog._connections_refreshed(  # noqa: SLF001 - verify rendered safe fields
        [
            {
                "connection_id": "connection-1",
                "provider": "smtp",
                "display_name": "Mail",
                "status": "active",
            }
        ]
    )
    assert dialog.connection_list.item(0).text() == "Mail · smtp · active"
    dialog.username.setText("owner@example.invalid")
    dialog.password.setText("test-secret")
    dialog.endpoint.setText("smtp.example.invalid")
    monkeypatch.setattr(dialog, "refresh_connections", lambda: None)
    dialog._mutation_succeeded({}, "saved")  # noqa: SLF001
    assert dialog.username.text() == ""
    assert dialog.password.text() == ""
    assert dialog.endpoint.text() == ""
    dialog.close()
    application.processEvents()
