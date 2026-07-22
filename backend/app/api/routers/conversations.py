from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from application.conversation_memory import ConversationMemoryService
from application.conversations import ConversationError, ConversationService
from infrastructure.database import get_session
from app.support.errors import AppError
from app.api.schemas import (
    ConversationActorRequest,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationMessageListResponse,
    ConversationResponse,
    conversation_message_response,
    conversation_response,
)

router = APIRouter()


@router.post(
    "/api/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: ConversationCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationResponse:
    """创建 conversation。

    Args:
        payload: payload 参数。
        session: session 参数。
    """
    try:
        item = await ConversationService(session).create(
            user_id=payload.user_id, title=payload.title
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    return conversation_response(item)


@router.get("/api/conversations", response_model=ConversationListResponse)
async def list_conversations(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationListResponse:
    """列出 conversations。

    Args:
        user_id: user_id 参数。
        session: session 参数。
    """
    try:
        items = await ConversationService(session).list_active(user_id)
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    return ConversationListResponse(
        items=[conversation_response(item) for item in items]
    )


@router.get(
    "/api/conversations/{conversation_id}/messages",
    response_model=ConversationMessageListResponse,
)
async def list_conversation_messages(
    conversation_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ConversationMessageListResponse:
    """列出 conversation messages。

    Args:
        conversation_id: conversation_id 参数。
        user_id: user_id 参数。
        session: session 参数。
        limit: limit 参数。
    """
    try:
        items = await ConversationService(session).list_messages(
            conversation_id=conversation_id, user_id=user_id, limit=limit
        )
        summary = await ConversationMemoryService(session).get_active_summary(
            conversation_id=conversation_id, user_id=user_id
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    return ConversationMessageListResponse(
        items=[conversation_message_response(item) for item in items],
        compacted=summary is not None,
        summary_updated_at=summary.updated_at if summary else None,
        summary_version=summary.summary_version if summary else None,
    )


@router.post(
    "/api/conversations/{conversation_id}/archive",
    response_model=ConversationResponse,
)
async def archive_conversation(
    conversation_id: str,
    payload: ConversationActorRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationResponse:
    """归档 conversation。

    Args:
        conversation_id: conversation_id 参数。
        payload: payload 参数。
        session: session 参数。
    """
    try:
        item = await ConversationService(session).archive(
            conversation_id=conversation_id, user_id=payload.user_id
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    return conversation_response(item)
