from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent.skill_management.store import MAX_ARCHIVE_BYTES
from capabilities import build_default_registry

from infrastructure.database import get_session
from app.support.errors import AppError
from app.api.schemas import (
    SkillActorRequest,
    SkillCreateRequest,
    SkillListResponse,
    SkillResponse,
    skill_response,
)
from domain.services import TaskServiceError
from agent.skill_management.lifecycle import SkillLifecycleError, SkillLifecycleService

router = APIRouter()


def raise_app_error(exc: TaskServiceError | SkillLifecycleError) -> None:
    raise AppError(
        code=exc.code,
        message="Task operation failed.",
        status_code=exc.status_code,
    ) from exc


def lifecycle_service(
    request: Request, session: AsyncSession
) -> SkillLifecycleService:
    store = request.app.state.managed_skill_store

    def refresh_registry() -> None:
        request.app.state.capability_registry = build_default_registry(
            store.builtin_root,
            managed_store=store,
        )

    return SkillLifecycleService(
        session,
        store=store,
        refresh_registry=refresh_registry,
    )


@router.get("/api/skills", response_model=SkillListResponse)
async def list_skills(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillListResponse:
    items = lifecycle_service(request, session).list_skills()
    return SkillListResponse(items=[skill_response(item) for item in items])


@router.post(
    "/api/skills",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill(
    payload: SkillCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillResponse:
    try:
        item = await lifecycle_service(request, session).create(
            user_id=payload.user_id,
            name=payload.name,
            display_name=payload.display_name,
            summary=payload.summary,
            instructions=payload.instructions,
        )
    except (TaskServiceError, SkillLifecycleError) as exc:
        raise_app_error(exc)
    return skill_response(item)


@router.post(
    "/api/skills/install",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
)
async def install_skill(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: Annotated[str, Form(min_length=1)],
    package: Annotated[UploadFile, File()],
) -> SkillResponse:
    try:
        content = await package.read(MAX_ARCHIVE_BYTES + 1)
    finally:
        await package.close()
    try:
        item = await lifecycle_service(request, session).install(
            user_id=user_id,
            package=content,
        )
    except (TaskServiceError, SkillLifecycleError) as exc:
        raise_app_error(exc)
    return skill_response(item)


async def set_skill_enabled(
    *,
    request: Request,
    session: AsyncSession,
    payload: SkillActorRequest,
    name: str,
    enabled: bool,
) -> SkillResponse:
    try:
        item = await lifecycle_service(request, session).set_enabled(
            user_id=payload.user_id,
            name=name,
            enabled=enabled,
        )
    except (TaskServiceError, SkillLifecycleError) as exc:
        raise_app_error(exc)
    return skill_response(item)


@router.post("/api/skills/{name}/enable", response_model=SkillResponse)
async def enable_skill(
    name: str,
    payload: SkillActorRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillResponse:
    return await set_skill_enabled(
        request=request,
        session=session,
        payload=payload,
        name=name,
        enabled=True,
    )


@router.post("/api/skills/{name}/disable", response_model=SkillResponse)
async def disable_skill(
    name: str,
    payload: SkillActorRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillResponse:
    return await set_skill_enabled(
        request=request,
        session=session,
        payload=payload,
        name=name,
        enabled=False,
    )


@router.delete(
    "/api/skills/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def uninstall_skill(
    name: str,
    user_id: Annotated[str, Query(min_length=1)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    try:
        await lifecycle_service(request, session).uninstall(
            user_id=user_id,
            name=name,
        )
    except (TaskServiceError, SkillLifecycleError) as exc:
        raise_app_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)




