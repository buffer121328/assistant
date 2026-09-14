from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.support.errors import AppError
from application.command_catalog import (
    CommandCatalogError,
    list_office_commands_for_user,
)
from channels.desktop.local.schemas import (
    LocalCommandCatalogResponse,
    LocalCommandDescriptorResponse,
)
from infrastructure.persistence.database import get_session

router = APIRouter()


@router.get("/commands/catalog", response_model=LocalCommandCatalogResponse)
async def local_command_catalog(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalCommandCatalogResponse:
    """Return only bounded office-facing command metadata for one local user.

    Args:
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    try:
        items = await list_office_commands_for_user(session, user_id)
    except CommandCatalogError as exc:
        raise AppError(exc.code, "Command catalog unavailable.", exc.status_code) from exc
    return LocalCommandCatalogResponse(
        items=[
            LocalCommandDescriptorResponse(
                command_id=item.command_id,
                label=item.label,
                description=item.description,
                selectable=item.selectable,
            )
            for item in items
        ]
    )
