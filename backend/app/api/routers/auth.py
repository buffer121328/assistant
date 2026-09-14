from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.auth import (
    LocalInitializationStatusResponse,
    LocalInitializeRequest,
    LocalLoginRequest,
    LocalRecoveryStatusResponse,
    LocalRecoverPasswordRequest,
    LocalSessionResponse,
)
from app.support.errors import AppError
from application.department_bootstrap import DepartmentBootstrapService
from application.enterprise_governance import resolve_local_subject
from domain.policies.enterprise import GovernanceValidationError, SubjectContext
from application.local_auth import LocalAuthError, LocalAuthenticationService
from domain.models import Organization, User
from infrastructure.persistence.database import get_session

router = APIRouter(prefix="/api/auth", tags=["local-auth"])


async def current_session_token(
    x_assistant_session: Annotated[str | None, Header(alias="X-Assistant-Session")] = None,
) -> str:
    """Read the opaque local session from a dedicated header, never a user ID.

    Args:
        x_assistant_session: 用于执行当前操作的 x assistant session 参数。
    """
    if not x_assistant_session or len(x_assistant_session) > 512:
        raise AppError(
            code="authentication_required",
            message="Authentication is required.",
            status_code=401,
        )
    return x_assistant_session


def raise_auth_error(exc: LocalAuthError) -> NoReturn:
    """Convert a bounded service failure into the public API error envelope.

    Args:
        exc: 用于执行当前操作的 exc 参数。
    """
    raise AppError(
        code=exc.code,
        message="Local authentication failed.",
        status_code=exc.status_code,
    ) from exc


async def authenticated_subject(
    request: Request,
    token: Annotated[str, Depends(current_session_token)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubjectContext:
    """Resolve the current subject from a session and reject a conflicting user claim.

    Args:
        request: 当前操作的结构化请求。
        token: 用于执行当前操作的 token 参数。
        session: 当前数据库异步会话。
    """
    try:
        auth_result = await LocalAuthenticationService(session).session_for_token(token)
    except LocalAuthError as exc:
        raise_auth_error(exc)
    claimed_user_id = request.query_params.get("user_id")
    if claimed_user_id is not None and claimed_user_id != auth_result.user_id:
        raise AppError(
            code="identity_claim_conflict",
            message="Authenticated identity does not match the request.",
            status_code=403,
        )
    return await resolve_local_subject(session, auth_result.user_id)


async def session_response(
    *,
    session: AsyncSession,
    auth_result: object,
    include_token: bool,
) -> LocalSessionResponse:
    """Build a server-resolved session response without leaking credential verifiers.

    Args:
        session: 当前数据库异步会话。
        auth_result: 用于执行当前操作的 auth result 参数。
        include_token: 用于执行当前操作的 include token 参数。
    """
    from application.local_auth import AuthenticatedSession

    if not isinstance(auth_result, AuthenticatedSession):
        raise RuntimeError("Unexpected authentication result")
    user = await session.get(User, auth_result.user_id)
    if user is None:
        raise AppError(code="authentication_required", message="Authentication is required.", status_code=401)
    subject = await resolve_local_subject(session, user.id)
    organizations = tuple(
        await session.scalars(
            select(Organization).where(Organization.id.in_(subject.organization_ids))
        )
    ) if subject.organization_ids else ()
    organization_names = sorted(
        organization.name
        for organization in organizations
        if organization.type == "department" and organization.status == "active"
    )
    return LocalSessionResponse(
        session_token=auth_result.token if include_token else None,
        expires_at=auth_result.expires_at,
        tenant_id=subject.tenant_id,
        user_id=subject.user_id,
        display_name=user.display_name,
        organization_ids=list(subject.organization_ids),
        organization_names=organization_names,
        roles=[role.value for role in subject.roles],
        authority_revision=subject.authority_revision,
    )


@router.get("/initialization-status", response_model=LocalInitializationStatusResponse)
async def initialization_status(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalInitializationStatusResponse:
    """Expose only whether the first local administrator can still be created.

    Args:
        session: 当前数据库异步会话。
    """
    return LocalInitializationStatusResponse(
        initialization_required=await LocalAuthenticationService(session).initialization_required()
    )


@router.post("/initialize", response_model=LocalSessionResponse, status_code=status.HTTP_201_CREATED)
async def initialize(
    request: Request,
    payload: LocalInitializeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalSessionResponse:
    """Create the first local enterprise administrator once.

    Args:
        payload: 当前操作的结构化载荷。
        session: 当前数据库异步会话。
    """
    service = LocalAuthenticationService(session)
    try:
        result = await service.initialize_administrator(
            display_name=payload.display_name,
            login_name=payload.login_name,
            password=payload.password,
        )
        subject = await resolve_local_subject(session, result.user_id)
        await DepartmentBootstrapService(
            session,
            capability_registry=request.app.state.capability_registry,
        ).bootstrap(subject)
        response = await session_response(session=session, auth_result=result, include_token=True)
        await session.commit()
        return response
    except LocalAuthError as exc:
        await session.rollback()
        raise_auth_error(exc)
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(
            code="initialization_unavailable",
            message="Local initialization failed.",
            status_code=409,
        ) from exc


@router.post("/login", response_model=LocalSessionResponse)
async def login(
    payload: LocalLoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalSessionResponse:
    """Authenticate a local account with bounded failures.

    Args:
        payload: 当前操作的结构化载荷。
        session: 当前数据库异步会话。
    """
    service = LocalAuthenticationService(session)
    try:
        result = await service.authenticate(login_name=payload.login_name, password=payload.password)
        response = await session_response(session=session, auth_result=result, include_token=True)
        await session.commit()
        return response
    except LocalAuthError as exc:
        await session.rollback()
        raise_auth_error(exc)


@router.get("/recovery-status", response_model=LocalRecoveryStatusResponse)
async def recovery_status(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalRecoveryStatusResponse:
    result = await LocalAuthenticationService(session).recovery_status()
    return LocalRecoveryStatusResponse(**result)


@router.post("/recover-password", response_model=LocalSessionResponse)
async def recover_password(
    payload: LocalRecoverPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalSessionResponse:
    try:
        result = await LocalAuthenticationService(session).recover_password(
            login_name=payload.login_name,
            answer=payload.answer,
            new_password=payload.new_password,
        )
        response = await session_response(session=session, auth_result=result, include_token=True)
        await session.commit()
        return response
    except LocalAuthError as exc:
        await session.rollback()
        raise_auth_error(exc)


@router.get("/session", response_model=LocalSessionResponse)
async def current_session(
    token: Annotated[str, Depends(current_session_token)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalSessionResponse:
    """Return current identity facts from a valid session without returning its token.

    Args:
        token: 用于执行当前操作的 token 参数。
        session: 当前数据库异步会话。
    """
    try:
        result = await LocalAuthenticationService(session).session_for_token(token)
        response = await session_response(session=session, auth_result=result, include_token=False)
        await session.commit()
        return response
    except LocalAuthError as exc:
        await session.rollback()
        raise_auth_error(exc)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    token: Annotated[str, Depends(current_session_token)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Revoke the active local session; repeated logout is safe.

    Args:
        token: 用于执行当前操作的 token 参数。
        session: 当前数据库异步会话。
    """
    await LocalAuthenticationService(session).logout(token)
    await session.commit()
