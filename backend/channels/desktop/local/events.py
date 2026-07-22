from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from channels.desktop.local.payloads import (
    local_event_response,
    local_tool_log_response,
)
from channels.desktop.local.schemas import LocalEventListResponse
from channels.desktop.local.services import get_owned_task, sequence_after_event_id
from domain.models import Task, TaskStatus, ToolLog
from infrastructure.persistence.database import get_session
from tasks.events import TaskEventRepository

router = APIRouter()


@router.get("/tasks/{task_id}/events", response_model=LocalEventListResponse)
async def local_list_task_events(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    after_event_id: str | None = None,
) -> LocalEventListResponse:
    """List sanitized local task events after an optional cursor."""
    await get_owned_task(session, task_id=task_id, user_id=user_id)
    after_sequence = await sequence_after_event_id(
        session,
        task_id=task_id,
        after_event_id=after_event_id,
    )
    events = await TaskEventRepository(session).list_after(
        task_id=task_id,
        after=after_sequence,
    )
    return LocalEventListResponse(
        items=[local_event_response(event) for event in events]
    )


@router.websocket("/tasks/{task_id}/events/stream")
async def local_stream_task_events(
    websocket: WebSocket,
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    after_event_id: str | None = None,
) -> None:
    """Stream task events to the desktop client until the task reaches a terminal state."""
    await websocket.accept()
    try:
        async with websocket.app.state.db_sessionmaker() as session:
            await get_owned_task(session, task_id=task_id, user_id=user_id)
            sequence = await sequence_after_event_id(
                session,
                task_id=task_id,
                after_event_id=after_event_id,
            )
        terminal = {
            TaskStatus.SUCCESS.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.WAITING_APPROVAL.value,
        }
        while True:
            async with websocket.app.state.db_sessionmaker() as session:
                events = await TaskEventRepository(session).list_after(
                    task_id=task_id,
                    after=sequence,
                )
                current = await session.get(Task, task_id)
            for event in events:
                sequence = event.sequence
                await websocket.send_json(local_event_response(event).model_dump())
            if current is None or (current.status in terminal and not events):
                await websocket.close()
                return
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        return


@router.get("/tasks/{task_id}/logs", response_model=LocalEventListResponse)
async def local_list_task_logs(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalEventListResponse:
    """List task tool logs through the local event envelope."""
    await get_owned_task(session, task_id=task_id, user_id=user_id)
    logs = list(
        await session.scalars(
            select(ToolLog)
            .where(ToolLog.task_id == task_id)
            .order_by(ToolLog.created_at.asc(), ToolLog.id.asc())
        )
    )
    return LocalEventListResponse(
        items=[
            local_tool_log_response(log, sequence=index)
            for index, log in enumerate(logs, 1)
        ]
    )
