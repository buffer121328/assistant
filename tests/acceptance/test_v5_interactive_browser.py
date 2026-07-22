from __future__ import annotations

from collections.abc import AsyncIterator
import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

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
    AccountBackedBrowserSessions,
    BrowserSession,
    CredentialCipher,
    ProviderError,
)
from tools import ToolInvocation, ToolRegistry, external_approval_binding
from tools.builtin.browser import BrowserDestinationError, PublicUrlPolicy
from tools.builtin.browser_interact import (
    BrowserInteractionResult,
    BrowserInteractor,
    build_browser_tool_descriptors,
    build_browser_tool_specs,
)


MASTER_KEY = "test-browser-master-key-with-more-than-32-chars"


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/browser.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def public_resolver(host: str) -> tuple[str, ...]:
    del host
    return ("93.184.216.34",)


async def private_resolver(host: str) -> tuple[str, ...]:
    del host
    return ("127.0.0.1",)


class FakeBrowserRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[dict[str, str], ...]]] = []

    async def execute(
        self,
        *,
        session: BrowserSession,
        url: str,
        actions: tuple[dict[str, str], ...],
        policy: PublicUrlPolicy,
        timeout_seconds: float,
        max_text_chars: int,
    ) -> tuple[BrowserInteractionResult, dict[str, Any]]:
        del session, policy, timeout_seconds, max_text_chars
        self.calls.append((url, actions))
        return (
            BrowserInteractionResult(
                title="Account",
                text="Signed in",
                final_url="https://example.com/account",
            ),
            {
                "cookies": [{"name": "session", "value": "new-cookie-secret"}],
                "origins": [],
            },
        )


@pytest.mark.asyncio
async def test_browser_state_is_owned_encrypted_allowlisted_and_explicitly_saved(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    cipher = CredentialCipher(MASTER_KEY)
    runtime = FakeBrowserRuntime()
    policy = PublicUrlPolicy(resolver=public_resolver)
    async with sessionmaker() as session:
        owner = User(display_name="Owner")
        other = User(display_name="Other")
        session.add_all((owner, other))
        await session.flush()
        connection = AccountConnection(
            user_id=owner.id,
            provider="browser",
            display_name="Example",
            credential_ciphertext=cipher.encrypt(
                {
                    "allowed_domains": json.dumps(["example.com"]),
                    "storage_state": json.dumps(
                        {
                            "cookies": [
                                {"name": "session", "value": "old-cookie-secret"}
                            ],
                            "origins": [],
                        }
                    ),
                }
            ),
            credential_version="fernet-v1",
            status="active",
        )
        session.add(connection)
        await session.commit()
        original_ciphertext = connection.credential_ciphertext
        interactor = BrowserInteractor(
            sessions=AccountBackedBrowserSessions(session, cipher=cipher),
            runtime=runtime,
            policy=policy,
        )
        result = await interactor.run(
            user_id=owner.id,
            connection_id=connection.id,
            url="https://example.com/login",
            actions=[{"type": "fill", "field": "Password", "value": "form-secret"}],
            save_state=False,
        )
        await session.refresh(connection)
        assert connection.credential_ciphertext == original_ciphertext
        assert "cookie-secret" not in repr(result)

        await interactor.run(
            user_id=owner.id,
            connection_id=connection.id,
            url="https://example.com/login",
            actions=[],
            save_state=True,
        )
        await session.refresh(connection)
        decrypted = cipher.decrypt(connection.credential_ciphertext)
        assert "new-cookie-secret" in json.dumps(decrypted)
        assert "new-cookie-secret" not in connection.credential_ciphertext
        audit = await session.scalar(
            select(ConnectionAuditLog).where(ConnectionAuditLog.action == "save_state")
        )
        assert audit is not None

        call_count = len(runtime.calls)
        with pytest.raises(ProviderError, match="connection_unavailable"):
            await interactor.run(
                user_id=other.id,
                connection_id=connection.id,
                url="https://example.com",
                actions=[],
                save_state=False,
            )
        with pytest.raises(BrowserDestinationError, match="allowlist"):
            await interactor.run(
                user_id=owner.id,
                connection_id=connection.id,
                url="https://outside.example",
                actions=[],
                save_state=False,
            )
        with pytest.raises(BrowserDestinationError, match="Unsupported"):
            await interactor.run(
                user_id=owner.id,
                connection_id=connection.id,
                url="https://example.com",
                actions=[{"type": "javascript", "selector": "body"}],
                save_state=False,
            )
        with pytest.raises(BrowserDestinationError, match="allowlist"):
            await interactor.run(
                user_id=owner.id,
                connection_id=connection.id,
                url="https://example.com",
                actions=[{"type": "navigate", "url": "https://outside.example"}],
                save_state=False,
            )
        private_interactor = BrowserInteractor(
            sessions=AccountBackedBrowserSessions(session, cipher=cipher),
            runtime=runtime,
            policy=PublicUrlPolicy(resolver=private_resolver),
        )
        with pytest.raises(BrowserDestinationError, match="Private or reserved"):
            await private_interactor.run(
                user_id=owner.id,
                connection_id=connection.id,
                url="http://127.0.0.1",
                actions=[],
                save_state=False,
            )
        assert len(runtime.calls) == call_count


@pytest.mark.asyncio
async def test_browser_tools_are_exact_l3_and_logs_redact_form_values(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    cipher = CredentialCipher(MASTER_KEY)
    runtime = FakeBrowserRuntime()
    async with sessionmaker() as session:
        user = User(display_name="Owner")
        session.add(user)
        await session.flush()
        connection = AccountConnection(
            user_id=user.id,
            provider="browser",
            display_name="Example",
            credential_ciphertext=cipher.encrypt(
                {
                    "allowed_domains": "example.com",
                    "storage_state": "{}",
                }
            ),
            credential_version="fernet-v1",
            status="active",
        )
        task = Task(
            user_id=user.id,
            platform="desktop",
            task_type="agent",
            input_text="login",
            status="running",
        )
        session.add_all((connection, task))
        await session.flush()
        arguments = {
            "connection_id": connection.id,
            "url": "https://example.com/login",
            "actions": [
                {"type": "fill", "field": "Password", "value": "form-secret"}
            ],
        }
        binding = external_approval_binding("browser.interact", arguments)
        session.add(
            Approval(
                task_id=task.id,
                tool_name="browser.interact",
                approval_type="tool",
                subject=binding.subject,
                status="approved",
                decided_by_user_id=user.id,
            )
        )
        await session.flush()
        interactor = BrowserInteractor(
            sessions=AccountBackedBrowserSessions(session, cipher=cipher),
            runtime=runtime,
            policy=PublicUrlPolicy(resolver=public_resolver),
        )
        registry = ToolRegistry(session=session)
        registry.register(build_browser_tool_specs(interactor)[0])
        result = await registry.execute(
            ToolInvocation(
                task_id=task.id,
                user_id=user.id,
                name="browser.interact",
                arguments=arguments,
            ),
            allowed_tools=(),
            approval_required_tools=("browser.interact",),
        )
        log = await session.scalar(
            select(ToolLog).where(ToolLog.tool_name == "browser.interact")
        )

    descriptors = build_browser_tool_descriptors(enabled=True)
    assert {item.name for item in descriptors} == {"browser.interact", "browser.save_state"}
    assert all(item.risk_level == "L3" and item.requires_approval for item in descriptors)
    assert result == {
        "title": "Account",
        "text": "Signed in",
        "final_url": "https://example.com/account",
    }
    assert log is not None
    assert "form-secret" not in (log.input_text or "")
    assert binding.fingerprint in (log.input_text or "")
