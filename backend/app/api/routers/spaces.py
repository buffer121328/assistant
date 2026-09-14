from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    SpaceActorRequest,
    SpaceCreateRequest,
    SpaceDetailResponse,
    SpaceListResponse,
    SpaceMembershipCreateRequest,
    SpaceMembershipResponse,
    SpaceResponse,
    space_membership_response,
    space_response,
)
from app.support.errors import AppError
from application.session_context.spaces import SpaceError, SpaceService
from infrastructure.persistence.database import get_session

router = APIRouter()


def _app_error(exc: SpaceError) -> AppError:
    """Map a safe Space service error to the shared API envelope.

    Args:
        exc: 用于执行当前操作的 exc 参数。
    """
    return AppError(exc.code, "Space operation failed.", exc.status_code)


@router.post(
    "/api/spaces", response_model=SpaceResponse, status_code=status.HTTP_201_CREATED
)
async def create_space(
    payload: SpaceCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpaceResponse:
    """Create a Space and return its owner-scoped representation.

    Args:
        payload: 当前操作的结构化载荷。
        session: 当前数据库异步会话。
    """
    service = SpaceService(session)
    try:
        item = await service.create(
            user_id=payload.user_id,
            name=payload.name,
            description=payload.description or "",
            organization_id=payload.organization_id,
        )
        membership = await service.get_membership(
            space_id=item.id, user_id=payload.user_id
        )
    except SpaceError as exc:
        raise _app_error(exc) from exc
    return space_response(item, membership)


@router.get("/api/spaces", response_model=SpaceListResponse)
async def list_spaces(
    user_id: Annotated[str, Query(min_length=1, max_length=36)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpaceListResponse:
    """List active Spaces visible through exact-user membership.

    Args:
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    service = SpaceService(session)
    try:
        items = await service.list_accessible(user_id)
        responses = [
            space_response(
                item,
                await service.get_membership(space_id=item.id, user_id=user_id),
            )
            for item in items
        ]
    except SpaceError as exc:
        raise _app_error(exc) from exc
    return SpaceListResponse(items=responses)


@router.get("/api/spaces/{space_id}", response_model=SpaceDetailResponse)
async def get_space(
    space_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=36)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpaceDetailResponse:
    """Return bounded Space detail after active membership authorization.

    Args:
        space_id: 用于执行当前操作的 space id 参数。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    service = SpaceService(session)
    try:
        item = await service.get_accessible(space_id=space_id, user_id=user_id)
        membership = await service.get_membership(space_id=space_id, user_id=user_id)
        members = await service.get_members(space_id=space_id, user_id=user_id)
    except SpaceError as exc:
        raise _app_error(exc) from exc
    base = space_response(item, membership)
    return SpaceDetailResponse(
        **base.model_dump(),
        members=[space_membership_response(member) for member in members],
    )


@router.post(
    "/api/spaces/{space_id}/members",
    response_model=SpaceMembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_space_member(
    space_id: str,
    payload: SpaceMembershipCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpaceMembershipResponse:
    """Add or reactivate one editor/viewer under owner authority.

    Args:
        space_id: 用于执行当前操作的 space id 参数。
        payload: 当前操作的结构化载荷。
        session: 当前数据库异步会话。
    """
    try:
        membership = await SpaceService(session).add_member(
            space_id=space_id,
            actor_user_id=payload.user_id,
            member_user_id=payload.member_user_id,
            role=payload.role,
        )
    except SpaceError as exc:
        raise _app_error(exc) from exc
    return space_membership_response(membership)


@router.post(
    "/api/spaces/{space_id}/members/{member_user_id}/revoke",
    response_model=SpaceMembershipResponse,
)
async def revoke_space_member(
    space_id: str,
    member_user_id: str,
    payload: SpaceActorRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpaceMembershipResponse:
    """Revoke one non-owner member without touching private user data.

    Args:
        space_id: 用于执行当前操作的 space id 参数。
        member_user_id: 用于执行当前操作的 member user id 参数。
        payload: 当前操作的结构化载荷。
        session: 当前数据库异步会话。
    """
    try:
        membership = await SpaceService(session).revoke_member(
            space_id=space_id,
            actor_user_id=payload.user_id,
            member_user_id=member_user_id,
        )
    except SpaceError as exc:
        raise _app_error(exc) from exc
    return space_membership_response(membership)


@router.post("/api/spaces/{space_id}/archive", response_model=SpaceResponse)
async def archive_space(
    space_id: str,
    payload: SpaceActorRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpaceResponse:
    """Archive one Space while retaining memberships and private history.

    Args:
        space_id: 用于执行当前操作的 space id 参数。
        payload: 当前操作的结构化载荷。
        session: 当前数据库异步会话。
    """
    service = SpaceService(session)
    try:
        item = await service.archive(space_id=space_id, user_id=payload.user_id)
        membership = await service.get_membership(
            space_id=space_id, user_id=payload.user_id
        )
    except SpaceError as exc:
        raise _app_error(exc) from exc
    return space_response(item, membership)
