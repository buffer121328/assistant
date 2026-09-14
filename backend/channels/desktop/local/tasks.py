from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.tasks import raise_app_error
from app.api.schemas import TaskListResponse, TaskResponse, task_response
from app.support.errors import AppError
from application.session_context.conversations import (
    ConversationError,
    ConversationService,
)
from application.session_context.spaces import SpaceError, SpaceService
from application.session_context.context_snapshots import (
    TaskContextSnapshotNotFoundError,
    TaskContextSnapshotService,
)
from application.session_context.resource_references import ResourceReferenceError
from application.task_execution.lifecycle import TaskService, TaskServiceError
from channels.desktop.local.schemas import (
    LocalConversationTokenStatsResponse,
    LocalMessageAppendRequest,
    LocalTaskContextResponse,
    LocalTaskCreateRequest,
    LocalTaskSubmissionResponse,
)
from channels.desktop.local.services import (
    get_owned_task,
    revoke_queued_task_execution,
    safe_enqueue_task_execution,
)
from infrastructure.persistence.database import get_session

router = APIRouter()


@router.get("/tasks", response_model=TaskListResponse)
async def local_list_tasks(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskListResponse:
    """List local desktop tasks for a user.

    Args:
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
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
    """Create a local desktop task and enqueue execution best-effort.

    Args:
        payload: 当前操作的结构化载荷。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
    """
    try:
        conversation_id = payload.conversation_id
        if conversation_id is None:
            if payload.space_id is None:
                raise AppError(
                    "workspace_required",
                    "Please select a Workspace before starting a new task.",
                    422,
                )
            await SpaceService(session).require_owned_active(
                space_id=payload.space_id,
                user_id=payload.user_id,
            )
            conversation = await ConversationService(session).create(
                user_id=payload.user_id,
                title=payload.input_text,
                channel="desktop",
                space_id=payload.space_id,
                commit=False,
            )
            conversation_id = conversation.id
        task = await TaskService(
            session,
            uploaded_resources_root=request.app.state.settings.resource_uploads_root,
            artifacts_root=request.app.state.settings.artifacts_root,
        ).create_task(
            user_id=payload.user_id,
            platform="local",
            task_type=payload.task_type,
            command_id=payload.command_id,
            input_text=payload.input_text,
            workflow_key=payload.workflow_key,
            model_class=payload.model_class,
            conversation_id=conversation_id,
            resource_reference_ids=tuple(payload.resource_reference_ids),
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    except SpaceError as exc:
        raise AppError(
            exc.code, "Workspace operation failed.", exc.status_code
        ) from exc
    except TaskServiceError as exc:
        raise_app_error(exc)
    except ResourceReferenceError as exc:
        raise AppError(exc.code, "Resource operation failed.", exc.status_code) from exc
    queued = safe_enqueue_task_execution(
        task.id,
        runtime_settings=request.app.state.settings,
        tenant_id=task.tenant_id,
        organization_ids=(task.organization_id,) if task.organization_id else (),
    )
    return LocalTaskSubmissionResponse(task=task_response(task), queued=queued)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def local_get_task(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResponse:
    """Return a local desktop task by id for its owner.

    Args:
        task_id: 目标任务 ID。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    task = await get_owned_task(session, task_id=task_id, user_id=user_id)
    return task_response(task)


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
async def local_cancel_task(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResponse:
    """Cancel an owned local task that has not reached a terminal state.

    Args:
        task_id: 目标任务 ID。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    await get_owned_task(session, task_id=task_id, user_id=user_id)
    try:
        task = await TaskService(session).cancel_task(task_id)
    except TaskServiceError as exc:
        raise_app_error(exc)
    revoke_queued_task_execution(task.id)
    return task_response(task)


@router.get("/tasks/{task_id}/context", response_model=LocalTaskContextResponse)
async def local_get_task_context(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalTaskContextResponse:
    """Return bounded Task context metadata only to the owning local user.

    Args:
        task_id: 目标任务 ID。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    try:
        snapshot = await TaskContextSnapshotService(session).get_owned(
            task_id=task_id,
            user_id=user_id,
        )
    except TaskContextSnapshotNotFoundError as exc:
        raise AppError("task_not_found", "Task not found.", 404) from exc
    return LocalTaskContextResponse(
        context_snapshot_id=snapshot.id,
        task_id=snapshot.task_id,
        conversation_id=snapshot.conversation_id,
        user_id=snapshot.user_id,
        tenant_id=snapshot.tenant_id,
        organization_id=snapshot.organization_id,
        owner_type=snapshot.owner_type,
        owner_id=snapshot.owner_id,
        visibility=snapshot.visibility,
        command_id=snapshot.command_id,
        state=cast(Literal["initial", "finalized"], snapshot.state),
        resource_reference_ids=list(snapshot.resource_reference_ids),
        resolved_resource_versions=snapshot.resolved_resource_versions,
        memory_scope_snapshot=list(snapshot.memory_scope_snapshot),
        knowledge_scope_snapshot=list(snapshot.knowledge_scope_snapshot),
        capability_snapshot=list(snapshot.capability_snapshot),
        agent_profile_schema_version=snapshot.agent_profile_schema_version,
        created_at=snapshot.created_at,
        finalized_at=snapshot.finalized_at,
    )


@router.post("/tasks/{task_id}/messages", response_model=LocalTaskSubmissionResponse)
async def local_append_task_message(
    task_id: str,
    payload: LocalMessageAppendRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalTaskSubmissionResponse:
    """Append a local message by creating the next task in the conversation.

    Args:
        task_id: 目标任务 ID。
        payload: 当前操作的结构化载荷。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
    """
    task = await get_owned_task(session, task_id=task_id, user_id=payload.user_id)
    conversation_id = task.conversation_id
    try:
        next_task = await TaskService(
            session,
            uploaded_resources_root=request.app.state.settings.resource_uploads_root,
            artifacts_root=request.app.state.settings.artifacts_root,
        ).create_task(
            user_id=payload.user_id,
            platform="local",
            task_type=task.task_type,
            command_id=payload.command_id,
            input_text=payload.content,
            workflow_key=task.workflow_key,
            model_class=task.model_class,
            conversation_id=conversation_id,
            resource_reference_ids=tuple(payload.resource_reference_ids),
        )
    except TaskServiceError as exc:
        raise_app_error(exc)
    except ResourceReferenceError as exc:
        raise AppError(exc.code, "Resource operation failed.", exc.status_code) from exc
    queued = safe_enqueue_task_execution(
        next_task.id,
        runtime_settings=request.app.state.settings,
        tenant_id=next_task.tenant_id,
        organization_ids=(next_task.organization_id,)
        if next_task.organization_id
        else (),
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
    """Return local conversation token statistics.

    Args:
        conversation_id: 目标会话 ID。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
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
        used_input_tokens=stats.used_input_tokens,
        used_output_tokens=stats.used_output_tokens,
        used_total_tokens=stats.used_total_tokens,
        reserved_input_tokens=stats.reserved_input_tokens,
        reserved_output_tokens=stats.reserved_output_tokens,
        reserved_total_tokens=stats.reserved_total_tokens,
        remaining_tokens=stats.remaining_tokens,
        available_tokens=stats.available_tokens,
        blocked_reason=stats.blocked_reason,
        usage_ratio=round(stats.usage_ratio, 4),
        status=cast(Literal["ok", "warning", "full"], stats.status),
    )


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def local_delete_task(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete / archive a local desktop task and its conversation.

    Args:
        task_id: 目标任务 ID。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    task = await get_owned_task(session, task_id=task_id, user_id=user_id)
    revoke_queued_task_execution(task.id)
    if task.conversation_id is None:
        return
    try:
        await ConversationService(session).archive(
            conversation_id=task.conversation_id, user_id=user_id
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
