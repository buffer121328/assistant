from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from integrations import CredentialCipher, CredentialError

from application.account_connections import AccountConnectionError, AccountConnectionService
from infrastructure.database import get_session
from app.support.errors import AppError
from app.api.schemas import (
    AccountConnectionActorRequest,
    AccountConnectionCreateRequest,
    AccountConnectionListResponse,
    AccountConnectionResponse,
    account_connection_response,
)

router = APIRouter()


def account_service(
    request: Request, session: AsyncSession
) -> AccountConnectionService:
    """处理 account service。

    Args:
        request: request 参数。
        session: session 参数。
    """
    try:
        cipher = CredentialCipher(
            request.app.state.settings.credential_master_key.get_secret_value()
        )
    except CredentialError as exc:
        raise AppError(
            code="credential_master_key_unavailable",
            message="Credential storage is not configured.",
            status_code=503,
        ) from exc
    return AccountConnectionService(
        session,
        cipher=cipher,
        tester=getattr(request.app.state, "connection_tester", None),
    )


def raise_account_error(exc: AccountConnectionError) -> None:
    """处理 raise account error。

    Args:
        exc: exc 参数。
    """
    raise AppError(
        code=exc.code,
        message="Account connection operation failed.",
        status_code=exc.status_code,
    ) from exc


@router.get("/api/connections", response_model=AccountConnectionListResponse)
async def list_connections(
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountConnectionListResponse:
    """列出 connections。

    Args:
        request: request 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
    items = await account_service(request, session).list(user_id)
    return AccountConnectionListResponse(
        items=[account_connection_response(item) for item in items]
    )


@router.post(
    "/api/connections",
    response_model=AccountConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    payload: AccountConnectionCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountConnectionResponse:
    """创建 connection。

    Args:
        payload: payload 参数。
        request: request 参数。
        session: session 参数。
    """
    try:
        item = await account_service(request, session).create(
            user_id=payload.user_id,
            provider=payload.provider,
            display_name=payload.display_name,
            credentials=payload.credentials,
        )
    except AccountConnectionError as exc:
        raise_account_error(exc)
    return account_connection_response(item)


async def update_connection_status(
    *,
    request: Request,
    session: AsyncSession,
    connection_id: str,
    user_id: str,
    new_status: str,
) -> AccountConnectionResponse:
    """更新 connection status。

    Args:
        request: request 参数。
        session: session 参数。
        connection_id: connection_id 参数。
        user_id: user_id 参数。
        new_status: new_status 参数。
    """
    try:
        item = await account_service(request, session).set_status(
            connection_id, user_id, new_status
        )
    except AccountConnectionError as exc:
        raise_account_error(exc)
    return account_connection_response(item)


@router.post(
    "/api/connections/{connection_id}/test",
    response_model=AccountConnectionResponse,
)
async def test_connection(
    connection_id: str,
    payload: AccountConnectionActorRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountConnectionResponse:
    """测试 connection。

    Args:
        connection_id: connection_id 参数。
        payload: payload 参数。
        request: request 参数。
        session: session 参数。
    """
    try:
        item = await account_service(request, session).test(
            connection_id, payload.user_id
        )
    except AccountConnectionError as exc:
        raise_account_error(exc)
    return account_connection_response(item)


@router.post(
    "/api/connections/{connection_id}/disable",
    response_model=AccountConnectionResponse,
)
async def disable_connection(
    connection_id: str,
    payload: AccountConnectionActorRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountConnectionResponse:
    """处理 disable connection。

    Args:
        connection_id: connection_id 参数。
        payload: payload 参数。
        request: request 参数。
        session: session 参数。
    """
    return await update_connection_status(
        request=request,
        session=session,
        connection_id=connection_id,
        user_id=payload.user_id,
        new_status="disabled",
    )


@router.delete(
    "/api/connections/{connection_id}",
    response_model=AccountConnectionResponse,
)
async def revoke_connection(
    connection_id: str,
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountConnectionResponse:
    """处理 revoke connection。

    Args:
        connection_id: connection_id 参数。
        request: request 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
    return await update_connection_status(
        request=request,
        session=session,
        connection_id=connection_id,
        user_id=user_id,
        new_status="revoked",
    )
