from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    RemoteControlBridgeSessionListResponse,
    RemoteControlBridgeSessionResponse,
    RemoteControlBridgeReplayResponse,
    remote_control_bridge_response,
)
from domain.models import ProcessedMessage, Task
from domain.services import ResultDispatcher
from channels.langbot.service import LangBotResultClient
from infrastructure.database import get_session
from app.support.errors import AppError

router = APIRouter()


@router.get(
    "/api/remote-control/bridge/sessions",
    response_model=RemoteControlBridgeSessionListResponse,
)
async def list_remote_control_bridge_sessions(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    conversation_id: Annotated[str | None, Query(min_length=1)] = None,
) -> RemoteControlBridgeSessionListResponse:
    """列出 remote control bridge sessions。

    Args:
        session: session 参数。
        limit: limit 参数。
        conversation_id: conversation_id 参数。
    """
    query = (
        select(ProcessedMessage, Task.status)
        .outerjoin(Task, Task.id == ProcessedMessage.task_id)
        .where(ProcessedMessage.platform == "langbot")
        .order_by(ProcessedMessage.created_at.desc(), ProcessedMessage.id.desc())
        .limit(limit)
    )
    if conversation_id is not None:
        query = query.where(ProcessedMessage.chat_id == conversation_id)

    result = await session.execute(query)
    items = [
        remote_control_bridge_response(message, task_status=task_status)
        for message, task_status in result.all()
    ]
    return RemoteControlBridgeSessionListResponse(items=items)


@router.get(
    "/api/remote-control/bridge/sessions/{message_id}",
    response_model=RemoteControlBridgeSessionResponse,
)
async def get_remote_control_bridge_session(
    message_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RemoteControlBridgeSessionResponse:
    """获取 remote control bridge session。

    Args:
        message_id: message_id 参数。
        session: session 参数。
    """
    result = await session.execute(
        select(ProcessedMessage, Task.status)
        .outerjoin(Task, Task.id == ProcessedMessage.task_id)
        .where(
            ProcessedMessage.platform == "langbot",
            ProcessedMessage.message_id == message_id,
        )
        .limit(1)
    )
    row = result.first()
    if row is None:
        raise AppError(
            code="bridge_session_not_found",
            message="Bridge session not found.",
            status_code=404,
        )
    message, task_status = row
    return remote_control_bridge_response(message, task_status=task_status)


@router.post(
    "/api/remote-control/bridge/sessions/{message_id}/replay",
    response_model=RemoteControlBridgeReplayResponse,
)
async def replay_remote_control_bridge_session(
    message_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RemoteControlBridgeReplayResponse:
    """处理 replay remote control bridge session。

    Args:
        message_id: message_id 参数。
        request: request 参数。
        session: session 参数。
    """
    result = await session.execute(
        select(ProcessedMessage, Task.status)
        .outerjoin(Task, Task.id == ProcessedMessage.task_id)
        .where(
            ProcessedMessage.platform == "langbot",
            ProcessedMessage.message_id == message_id,
        )
        .limit(1)
    )
    row = result.first()
    if row is None:
        raise AppError(
            code="bridge_session_not_found",
            message="Bridge session not found.",
            status_code=404,
        )

    message, _task_status = row
    if message.task_id is None:
        raise AppError(
            code="bridge_session_not_replayable",
            message="Bridge session is not linked to a task.",
            status_code=409,
        )

    dispatch_result = await ResultDispatcher(
        session,
        langbot_client=LangBotResultClient(request.app.state.settings),
    ).dispatch_task(message.task_id)

    refreshed = await session.execute(
        select(ProcessedMessage, Task.status)
        .outerjoin(Task, Task.id == ProcessedMessage.task_id)
        .where(
            ProcessedMessage.platform == "langbot",
            ProcessedMessage.message_id == message_id,
        )
        .limit(1)
    )
    refreshed_row = refreshed.first()
    assert refreshed_row is not None
    refreshed_message, refreshed_status = refreshed_row
    return RemoteControlBridgeReplayResponse(
        dispatch_status=dispatch_result.status,
        message=dispatch_result.message,
        session=remote_control_bridge_response(
            refreshed_message,
            task_status=refreshed_status,
        ),
    )
