from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routers.auth import authenticated_subject
from app.support.errors import AppError
from application.local_auth import LocalAuthError, LocalAuthenticationService
from domain.models import Base, GovernanceAudit, LocalCredential, LocalSession


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as current:
        yield current
    await engine.dispose()


@pytest.mark.asyncio
async def test_initialization_persists_only_password_and_session_digests(session: AsyncSession) -> None:
    service = LocalAuthenticationService(session)

    result = await service.initialize_administrator(
        display_name="企业管理员",
        login_name="admin.local",
        password="correct-horse-battery-staple",
    )
    await session.commit()

    credential = await session.scalar(select(LocalCredential))
    stored_session = await session.scalar(select(LocalSession))
    audit_rows = tuple(await session.scalars(select(GovernanceAudit)))

    assert result.token
    assert credential is not None
    assert stored_session is not None
    assert credential.password_verifier.startswith("scrypt.v1$")
    assert "correct-horse-battery-staple" not in credential.password_verifier
    assert result.token != stored_session.token_digest
    assert all("correct-horse-battery-staple" not in row.summary for row in audit_rows)
    assert await service.initialization_required() is False


@pytest.mark.asyncio
async def test_login_failure_is_bounded_and_logout_revokes_session(session: AsyncSession) -> None:
    service = LocalAuthenticationService(session)
    initialized = await service.initialize_administrator(
        display_name="企业管理员",
        login_name="admin.local",
        password="correct-horse-battery-staple",
    )
    await session.commit()

    with pytest.raises(LocalAuthError) as wrong_password:
        await service.authenticate(login_name="admin.local", password="not-the-right-password")
    with pytest.raises(LocalAuthError) as unknown_login:
        await service.authenticate(login_name="unknown.user", password="not-the-right-password")

    assert wrong_password.value.code == unknown_login.value.code == "invalid_credentials"
    credential = await session.scalar(select(LocalCredential))
    assert credential is not None
    assert credential.failed_attempt_count == 1

    resolved = await service.session_for_token(initialized.token)
    assert resolved.user_id == initialized.user_id
    await service.logout(initialized.token)
    await session.commit()

    with pytest.raises(LocalAuthError) as revoked:
        await service.session_for_token(initialized.token)
    assert revoked.value.code == "session_invalid"


@pytest.mark.asyncio
async def test_session_subject_rejects_conflicting_user_claim(session: AsyncSession) -> None:
    service = LocalAuthenticationService(session)
    initialized = await service.initialize_administrator(
        display_name="企业管理员",
        login_name="admin.local",
        password="correct-horse-battery-staple",
    )
    await session.commit()

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/admin/organizations",
            "headers": [],
            "query_string": b"",
        }
    )
    subject = await authenticated_subject(request, initialized.token, session)
    assert subject.user_id == initialized.user_id
    assert "enterprise_admin" in {role.value for role in subject.roles}

    conflicting_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/admin/organizations",
            "headers": [],
            "query_string": b"user_id=someone-else",
        }
    )
    with pytest.raises(AppError) as conflicting_claim:
        await authenticated_subject(conflicting_request, initialized.token, session)
    assert conflicting_claim.value.code == "identity_claim_conflict"
