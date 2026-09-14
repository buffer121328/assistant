from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.support.errors import AppError
from application.session_context.conversations import (
    ConversationError,
    ConversationService,
    ConversationWorkSummary,
)
from application.session_context.spaces import SpaceError
from channels.desktop.local.schemas import (
    LocalConversationSpaceUpdateRequest,
    LocalConversationWorkSummaryResponse,
)
from infrastructure.persistence.database import get_session

router = APIRouter()


def _summary_response(summary: ConversationWorkSummary) -> LocalConversationWorkSummaryResponse:
    """Serialize the service-owned summary without adding client authority.

    Args:
        summary: 用于执行当前操作的 summary 参数。
    """
    return LocalConversationWorkSummaryResponse(
        conversation_id=summary.conversation_id,
        scope_kind=cast(Literal["personal", "space"], summary.scope_kind),
        scope_name=summary.scope_name,
        space_id=summary.space_id,
        context_count=summary.context_count,
        artifact_count=summary.artifact_count,
        pending_approval_count=summary.pending_approval_count,
    )


@router.get(
    "/conversations/{conversation_id}/work-summary",
    response_model=LocalConversationWorkSummaryResponse,
)
async def local_conversation_work_summary(
    conversation_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=36)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalConversationWorkSummaryResponse:
    """Return an owner-scoped Personal Work or Space summary.

    Args:
        conversation_id: 目标会话 ID。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    try:
        summary = await ConversationService(session).work_summary(
            conversation_id=conversation_id, user_id=user_id
        )
    except (ConversationError, SpaceError) as exc:
        raise AppError(exc.code, "Conversation operation failed.", exc.status_code) from exc
    return _summary_response(summary)


@router.patch(
    "/conversations/{conversation_id}/space",
    response_model=LocalConversationWorkSummaryResponse,
)
async def local_update_conversation_space(
    conversation_id: str,
    payload: LocalConversationSpaceUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalConversationWorkSummaryResponse:
    """Move an owned Conversation into an editable Space or back to Personal Work.

    Args:
        conversation_id: 目标会话 ID。
        payload: 当前操作的结构化载荷。
        session: 当前数据库异步会话。
    """
    service = ConversationService(session)
    try:
        await service.set_space(
            conversation_id=conversation_id, user_id=payload.user_id, space_id=payload.space_id
        )
        summary = await service.work_summary(
            conversation_id=conversation_id, user_id=payload.user_id
        )
    except (ConversationError, SpaceError) as exc:
        raise AppError(exc.code, "Conversation operation failed.", exc.status_code) from exc
    return _summary_response(summary)


__all__ = ["router"]
