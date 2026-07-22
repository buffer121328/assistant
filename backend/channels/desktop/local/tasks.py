from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.tasks import raise_app_error
from app.api.schemas import TaskListResponse, TaskResponse, task_response
from app.support.errors import AppError
from session.conversations import ConversationError, ConversationService
from tasks.lifecycle import TaskService, TaskServiceError
from channels.desktop.local.schemas import (
    LocalConversationTokenStatsResponse,
    LocalMessageAppendRequest,
    LocalTaskCreateRequest,
    LocalTaskSubmissionResponse,
)
from channels.desktop.local.services import get_owned_task, safe_enqueue_task_execution
from infrastructure.persistence.database import get_session
from tools.builtin.workspace import SessionWorkspaceStore

router = APIRouter()


@router.get("/tasks", response_model=TaskListResponse)
async def local_list_tasks(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskListResponse:
    """List local desktop tasks for a user."""
    try:
        tasks = await TaskService(session).list_tasks(user_id)
    except TaskServiceError as exc:
        raise_app_error(exc)
    return TaskListResponse(items=[task_response(task) for task in tasks])


@router.post(
    "/tasks",
    response_model=LocalTaskSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def local_create_task(
    payload: LocalTaskCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalTaskSubmissionResponse:
    """Create a local desktop task and enqueue execution best-effort."""
    try:
        conversation_id = payload.conversation_id
        if conversation_id is None:
            conversation = await ConversationService(session).create(
                user_id=payload.user_id,
                title=payload.input_text,
                channel="desktop",
                commit=False,
            )
            conversation_id = conversation.id
        SessionWorkspaceStore(request.app.state.settings.session_workspace_root).create(
            session_id=conversation_id
        )
        task = await TaskService(session).create_task(
            user_id=payload.user_id,
            platform="local",
            task_type=payload.task_type,
            input_text=payload.input_text,
            workflow_key=payload.workflow_key,
            model_class=payload.model_class,
            conversation_id=conversation_id,
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    except TaskServiceError as exc:
        raise_app_error(exc)
    queued = safe_enqueue_task_execution(
        task.id,
        runtime_settings=request.app.state.settings,
    )
    return LocalTaskSubmissionResponse(task=task_response(task), queued=queued)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def local_get_task(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResponse:
    """Return a local desktop task by id for its owner."""
    task = await get_owned_task(session, task_id=task_id, user_id=user_id)
    return task_response(task)


@router.post("/tasks/{task_id}/messages", response_model=LocalTaskSubmissionResponse)
async def local_append_task_message(
    task_id: str,
    payload: LocalMessageAppendRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalTaskSubmissionResponse:
    """Append a local message by creating the next task in the conversation."""
    task = await get_owned_task(session, task_id=task_id, user_id=payload.user_id)
    conversation_id = task.conversation_id
    if conversation_id is not None:
        SessionWorkspaceStore(request.app.state.settings.session_workspace_root).create(
            session_id=conversation_id
        )
    try:
        next_task = await TaskService(session).create_task(
            user_id=payload.user_id,
            platform="local",
            task_type=task.task_type,
            input_text=payload.content,
            workflow_key=task.workflow_key,
            model_class=task.model_class,
            conversation_id=conversation_id,
        )
    except TaskServiceError as exc:
        raise_app_error(exc)
    queued = safe_enqueue_task_execution(
        next_task.id,
        runtime_settings=request.app.state.settings,
    )
    return LocalTaskSubmissionResponse(task=task_response(next_task), queued=queued)


@router.get(
    "/conversations/{conversation_id}/token-stats",
    response_model=LocalConversationTokenStatsResponse,
)
async def local_conversation_token_stats(
    conversation_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalConversationTokenStatsResponse:
    """Return local conversation token statistics."""
    try:
        stats = await ConversationService(session).token_stats(
            conversation_id=conversation_id, user_id=user_id
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    return LocalConversationTokenStatsResponse(
        conversation_id=stats.conversation_id,
        message_count=stats.message_count,
        user_message_count=stats.user_message_count,
        assistant_message_count=stats.assistant_message_count,
        total_estimated_tokens=stats.total_estimated_tokens,
        user_estimated_tokens=stats.user_estimated_tokens,
        assistant_estimated_tokens=stats.assistant_estimated_tokens,
        token_limit=stats.token_limit,
        usage_ratio=round(stats.usage_ratio, 4),
        status=cast(Literal["ok", "warning", "full"], stats.status),
    )
