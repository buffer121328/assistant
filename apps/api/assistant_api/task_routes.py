from __future__ import annotations

import asyncio
import json
import sys
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .conversations import ConversationError
from .database import get_session
from .errors import AppError
from .models import ApprovalStatus, Task, TaskStatus
from .schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalListResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
    TaskSubmissionResponse,
    approval_response,
    task_response,
)
from .services import ApprovalService, TaskService, TaskServiceError
from .task_events import TaskEventRepository, event_record
from .worker import enqueue_task_execution as _default_enqueue_task_execution

router = APIRouter()


def _enqueue_task_execution(task_id: str, *, runtime_settings: Any = None) -> bool:
    routes_module = sys.modules.get("assistant_api.routes")
    enqueue = getattr(routes_module, "enqueue_task_execution", None)
    if enqueue is None:
        enqueue = _default_enqueue_task_execution
    return bool(enqueue(task_id, runtime_settings=runtime_settings))


def raise_app_error(exc: TaskServiceError) -> None:
    raise AppError(
        code=exc.code,
        message="Task operation failed.",
        status_code=exc.status_code,
    ) from exc


@router.post(
    "/api/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    payload: TaskCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResponse:
    try:
        task = await TaskService(session).create_task(
            user_id=payload.user_id,
            platform=payload.platform,
            task_type=payload.task_type,
            input_text=payload.input_text,
            workflow_key=payload.workflow_key,
            model_class=payload.model_class,
            conversation_id=payload.conversation_id,
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    except TaskServiceError as exc:
        raise_app_error(exc)
    return task_response(task)


@router.post(
    "/api/tasks/submit",
    response_model=TaskSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_task(
    payload: TaskCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskSubmissionResponse:
    try:
        task = await TaskService(session).create_task(
            user_id=payload.user_id,
            platform=payload.platform,
            task_type=payload.task_type,
            input_text=payload.input_text,
            workflow_key=payload.workflow_key,
            model_class=payload.model_class,
            conversation_id=payload.conversation_id,
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    except TaskServiceError as exc:
        raise_app_error(exc)
    queued = _enqueue_task_execution(
        task.id,
        runtime_settings=request.app.state.settings,
    )
    return TaskSubmissionResponse(task=task_response(task), queued=queued)


@router.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskListResponse:
    try:
        tasks = await TaskService(session).list_tasks(user_id)
    except TaskServiceError as exc:
        raise_app_error(exc)
    return TaskListResponse(items=[task_response(task) for task in tasks])


@router.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResponse:
    try:
        task = await TaskService(session).get_task(task_id)
    except TaskServiceError as exc:
        raise_app_error(exc)
    return task_response(task)


@router.get("/api/tasks/{task_id}/events/stream")
async def stream_task_events(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    )
    if task is None:
        raise AppError("task_not_found", "Task not found.", 404)

    async def records():
        sequence = after
        terminal = {
            TaskStatus.SUCCESS.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.WAITING_APPROVAL.value,
        }
        while not await request.is_disconnected():
            async with request.app.state.db_sessionmaker() as event_session:
                items = await TaskEventRepository(event_session).list_after(
                    task_id=task_id, after=sequence
                )
                current = await event_session.get(Task, task_id)
            for item in items:
                sequence = item.sequence
                yield json.dumps(event_record(item), ensure_ascii=False) + "\n"
            if current is None or (current.status in terminal and not items):
                return
            await asyncio.sleep(0.2)

    return StreamingResponse(records(), media_type="application/x-ndjson")


@router.get(
    "/api/tasks/{task_id}/approvals",
    response_model=ApprovalListResponse,
)
async def list_task_approvals(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalListResponse:
    try:
        approvals = await ApprovalService(session).list_for_owner(
            task_id=task_id,
            user_id=user_id,
        )
    except TaskServiceError as exc:
        raise_app_error(exc)
    return ApprovalListResponse(
        items=[approval_response(approval) for approval in approvals]
    )


@router.post(
    "/api/tasks/{task_id}/approvals/{approval_id}/decision",
    response_model=ApprovalDecisionResponse,
)
async def decide_task_approval(
    task_id: str,
    approval_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalDecisionResponse:
    try:
        result = await ApprovalService(session).decide(
            task_id=task_id,
            approval_id=approval_id,
            user_id=payload.user_id,
            decision=ApprovalStatus(payload.decision),
        )
    except TaskServiceError as exc:
        raise_app_error(exc)

    queued = False
    if result.changed and result.approval.status == ApprovalStatus.APPROVED.value:
        queued = _enqueue_task_execution(
            result.task.id,
            runtime_settings=request.app.state.settings,
        )
    return ApprovalDecisionResponse(
        approval=approval_response(result.approval),
        task=task_response(result.task),
        queued=queued,
    )
