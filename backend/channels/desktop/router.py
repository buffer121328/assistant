from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated, Literal, cast
from urllib.parse import urlsplit, urlunsplit

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model_gateway import sanitize_text

from domain.conversations import ConversationError, ConversationService
from agent.tool_management.workspace import SessionWorkspaceStore
from infrastructure.database import get_session
from app.support.errors import AppError
from domain.models import ApprovalStatus, Task, TaskEvent, TaskStatus, ToolLog
from app.api.schemas import (
    ApprovalListResponse,
    ApprovalResponse,
    TaskListResponse,
    TaskResponse,
    approval_response,
    task_response,
)
from domain.services import ApprovalService, TaskService, TaskServiceError
from domain.task_events import TaskEventRepository
from app.api.routers.tasks import _enqueue_task_execution, raise_app_error


router = APIRouter(prefix="/local")
LOGGER = logging.getLogger("assistant_api")


class LocalTaskCreateRequest(BaseModel):
    """表示 处理 local task create request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    input_text: str = Field(min_length=1)
    workflow_key: str | None = None
    model_class: Literal["light", "standard"] | None = None
    conversation_id: str | None = None


class LocalTaskSubmissionResponse(BaseModel):
    """表示 处理 local task submission response 的后端数据结构或服务对象。"""

    task: TaskResponse
    queued: bool


class LocalEventResponse(BaseModel):
    """表示 处理 local event response 的后端数据结构或服务对象。"""

    event_id: str
    task_id: str
    type: str
    created_at: str
    sequence: int
    payload: dict[str, object]


class LocalEventListResponse(BaseModel):
    """表示 处理 local event list response 的后端数据结构或服务对象。"""

    items: list[LocalEventResponse]


class LocalConversationTokenStatsResponse(BaseModel):
    """表示 处理 local conversation token stats response 的后端数据结构或服务对象。"""

    conversation_id: str
    message_count: int
    user_message_count: int
    assistant_message_count: int
    total_estimated_tokens: int
    user_estimated_tokens: int
    assistant_estimated_tokens: int
    token_limit: int
    usage_ratio: float
    status: Literal["ok", "warning", "full"]


class LocalMessageAppendRequest(BaseModel):
    """表示 处理 local message append request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)
    content: str = Field(min_length=1)


class LocalApprovalDecisionRequest(BaseModel):
    """表示 处理 local approval decision request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=1000)


class LocalApprovalDecisionResponse(BaseModel):
    """表示 处理 local approval decision response 的后端数据结构或服务对象。"""

    approval: ApprovalResponse
    task: TaskResponse
    queued: bool


class LocalSettingsValidationRequest(BaseModel):
    """表示 处理 local settings validation request 的后端数据结构或服务对象。"""

    api_base_url: str = Field(min_length=1, max_length=500)
    default_workdir: str | None = Field(default=None, max_length=2000)
    default_model_class: Literal["light", "standard"] | None = None
    approval_policy: Literal["ask", "require_high_risk", "read_only"]


class LocalSettingsValidationResponse(BaseModel):
    """表示 处理 local settings validation response 的后端数据结构或服务对象。"""

    ok: bool
    settings: dict[str, object]


@router.get("/health")
def local_health(request: Request) -> dict[str, str]:
    """处理 local health。

    Args:
        request: request 参数。
    """
    return {
        "service_name": request.app.state.settings.service_name,
        "status": "ok",
    }


@router.get("/config")
def local_config(request: Request) -> dict[str, object]:
    """处理 local config。

    Args:
        request: request 参数。
    """
    settings = request.app.state.settings
    return {
        "service_name": settings.service_name,
        "app_env": settings.app_env,
        "local_api_auth_required": settings.local_api_auth_required,
        "features": {
            "browser_enabled": settings.browser_enabled,
            "sandbox_provider": settings.effective_sandbox_provider,
            "shell_exec_enabled": settings.effective_shell_exec_enabled,
            "sandbox_enabled": settings.effective_sandbox_provider != "none",
            "subagent_enabled": settings.subagent_enabled,
        },
    }


@router.post("/settings/validate", response_model=LocalSettingsValidationResponse)
def local_validate_settings(
    payload: LocalSettingsValidationRequest,
) -> LocalSettingsValidationResponse:
    """处理 local validate settings。

    Args:
        payload: payload 参数。
    """
    return LocalSettingsValidationResponse(
        ok=True,
        settings={
            "api_base_url": _validated_local_api_base_url(payload.api_base_url),
            "default_workdir": _validated_workdir(payload.default_workdir),
            "default_model_class": payload.default_model_class,
            "approval_policy": payload.approval_policy,
        },
    )


@router.get("/tasks", response_model=TaskListResponse)
async def local_list_tasks(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskListResponse:
    """处理 local list tasks。

    Args:
        user_id: user_id 参数。
        session: session 参数。
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
    """处理 local create task。

    Args:
        payload: payload 参数。
        request: request 参数。
        session: session 参数。
    """
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
    queued = _safe_enqueue_task_execution(
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
    """处理 local get task。

    Args:
        task_id: task_id 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
    task = await _get_owned_task(session, task_id=task_id, user_id=user_id)
    return task_response(task)


@router.post("/tasks/{task_id}/messages", response_model=LocalTaskSubmissionResponse)
async def local_append_task_message(
    task_id: str,
    payload: LocalMessageAppendRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalTaskSubmissionResponse:
    """处理 local append task message。

    Args:
        task_id: task_id 参数。
        payload: payload 参数。
        request: request 参数。
        session: session 参数。
    """
    task = await _get_owned_task(session, task_id=task_id, user_id=payload.user_id)
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
    queued = _safe_enqueue_task_execution(
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
    """处理 local conversation token stats。

    Args:
        conversation_id: conversation_id 参数。
        user_id: user_id 参数。
        session: session 参数。
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
        usage_ratio=round(stats.usage_ratio, 4),
        status=cast(Literal["ok", "warning", "full"], stats.status),
    )


@router.get("/tasks/{task_id}/events", response_model=LocalEventListResponse)
async def local_list_task_events(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    after_event_id: str | None = None,
) -> LocalEventListResponse:
    """处理 local list task events。

    Args:
        task_id: task_id 参数。
        user_id: user_id 参数。
        session: session 参数。
        after_event_id: after_event_id 参数。
    """
    await _get_owned_task(session, task_id=task_id, user_id=user_id)
    after_sequence = await _sequence_after_event_id(
        session,
        task_id=task_id,
        after_event_id=after_event_id,
    )
    events = await TaskEventRepository(session).list_after(
        task_id=task_id,
        after=after_sequence,
    )
    return LocalEventListResponse(
        items=[_local_event_response(event) for event in events]
    )


@router.websocket("/tasks/{task_id}/events/stream")
async def local_stream_task_events(
    websocket: WebSocket,
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    after_event_id: str | None = None,
) -> None:
    """处理 local stream task events。

    Args:
        websocket: websocket 参数。
        task_id: task_id 参数。
        user_id: user_id 参数。
        after_event_id: after_event_id 参数。
    """
    await websocket.accept()
    try:
        async with websocket.app.state.db_sessionmaker() as session:
            await _get_owned_task(session, task_id=task_id, user_id=user_id)
            sequence = await _sequence_after_event_id(
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
                await websocket.send_json(_local_event_response(event).model_dump())
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
    """处理 local list task logs。

    Args:
        task_id: task_id 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
    await _get_owned_task(session, task_id=task_id, user_id=user_id)
    logs = list(
        await session.scalars(
            select(ToolLog)
            .where(ToolLog.task_id == task_id)
            .order_by(ToolLog.created_at.asc(), ToolLog.id.asc())
        )
    )
    return LocalEventListResponse(
        items=[
            _local_tool_log_response(log, sequence=index)
            for index, log in enumerate(logs, 1)
        ]
    )


@router.get(
    "/tasks/{task_id}/approvals",
    response_model=ApprovalListResponse,
)
async def local_list_task_approvals(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalListResponse:
    """处理 local list task approvals。

    Args:
        task_id: task_id 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
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
    "/tasks/{task_id}/approvals/{approval_id}",
    response_model=LocalApprovalDecisionResponse,
)
async def local_decide_task_approval(
    task_id: str,
    approval_id: str,
    payload: LocalApprovalDecisionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalApprovalDecisionResponse:
    """处理 local decide task approval。

    Args:
        task_id: task_id 参数。
        approval_id: approval_id 参数。
        payload: payload 参数。
        request: request 参数。
        session: session 参数。
    """
    decision = (
        ApprovalStatus.APPROVED
        if payload.decision == "approve"
        else ApprovalStatus.REJECTED
    )
    try:
        result = await ApprovalService(session).decide(
            task_id=task_id,
            approval_id=approval_id,
            user_id=payload.user_id,
            decision=decision,
        )
    except TaskServiceError as exc:
        raise_app_error(exc)

    queued = False
    if result.changed and result.approval.status == ApprovalStatus.APPROVED.value:
        queued = _safe_enqueue_task_execution(
            result.task.id,
            runtime_settings=request.app.state.settings,
        )
    return LocalApprovalDecisionResponse(
        approval=approval_response(result.approval),
        task=task_response(result.task),
        queued=queued,
    )


async def _get_owned_task(session: AsyncSession, *, task_id: str, user_id: str) -> Task:
    """执行 获取 owned task 的内部辅助逻辑。

    Args:
        session: session 参数。
        task_id: task_id 参数。
        user_id: user_id 参数。
    """
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    )
    if task is None:
        raise AppError("task_not_found", "Task not found.", 404)
    return task


async def _sequence_after_event_id(
    session: AsyncSession,
    *,
    task_id: str,
    after_event_id: str | None,
) -> int:
    """执行 处理 sequence after event id 的内部辅助逻辑。

    Args:
        session: session 参数。
        task_id: task_id 参数。
        after_event_id: after_event_id 参数。
    """
    if after_event_id is None:
        return 0
    event = await session.scalar(
        select(TaskEvent).where(
            TaskEvent.task_id == task_id, TaskEvent.id == after_event_id
        )
    )
    if event is None:
        raise AppError("event_cursor_not_found", "Event cursor not found.", 404)
    return event.sequence


def _local_event_response(event: TaskEvent) -> LocalEventResponse:
    """执行 处理 local event response 的内部辅助逻辑。

    Args:
        event: event 参数。
    """
    return LocalEventResponse(
        event_id=event.id,
        task_id=event.task_id,
        type=event.event_type,
        created_at=event.created_at.isoformat(),
        sequence=event.sequence,
        payload=_safe_payload(event.payload_json),
    )


def _local_tool_log_response(log: ToolLog, *, sequence: int) -> LocalEventResponse:
    """执行 处理 local tool log response 的内部辅助逻辑。

    Args:
        log: log 参数。
        sequence: sequence 参数。
    """
    return LocalEventResponse(
        event_id=f"tool-log-{log.id}",
        task_id=log.task_id or "",
        type="task.log.appended",
        created_at=log.created_at.isoformat(),
        sequence=sequence,
        payload={
            "tool_name": sanitize_text(log.tool_name),
            "status": sanitize_text(log.status),
            "input": _safe_payload_value(log.input_text),
            "output": _safe_payload_value(log.output_text),
            "error": _safe_payload_value(log.error_message),
        },
    )


def _safe_payload(payload_json: str) -> dict[str, object]:
    """执行 处理 safe payload 的内部辅助逻辑。

    Args:
        payload_json: payload_json 参数。
    """
    import json

    loaded = json.loads(payload_json)
    if isinstance(loaded, dict):
        return {
            str(key): _safe_payload_value(value)
            for key, value in loaded.items()
            if not _is_sensitive_key(str(key))
        }
    return {"value": _safe_payload_value(loaded)}


def _safe_payload_value(value: object) -> object:
    """执行 处理 safe payload value 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {
            str(key): _safe_payload_value(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [_safe_payload_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_payload_value(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return sanitize_text(value)


def _is_sensitive_key(key: str) -> bool:
    """执行 处理 is sensitive key 的内部辅助逻辑。

    Args:
        key: key 参数。
    """
    normalized = key.casefold()
    return any(
        marker in normalized
        for marker in (
            "authorization",
            "cookie",
            "api_key",
            "apikey",
            "token",
            "secret",
        )
    )


def _safe_enqueue_task_execution(task_id: str, *, runtime_settings: object) -> bool:
    """执行 处理 safe enqueue task execution 的内部辅助逻辑。

    Args:
        task_id: task_id 参数。
        runtime_settings: runtime_settings 参数。
    """
    try:
        return _enqueue_task_execution(task_id, runtime_settings=runtime_settings)
    except Exception:
        LOGGER.warning("local_task_enqueue_failed", exc_info=True)
        return False


def _validated_local_api_base_url(value: str) -> str:
    """执行 处理 validated local api base url 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise AppError(
            "invalid_local_api_base_url",
            "Local API base URL must point to localhost.",
            400,
        )
    path = parsed.path.rstrip("/")
    if path not in {"", "/"}:
        raise AppError(
            "invalid_local_api_base_url",
            "Local API base URL must not include a path.",
            400,
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _validated_workdir(value: str | None) -> str | None:
    """执行 处理 validated workdir 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if value is None or not value.strip():
        return None
    candidate = Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise AppError(
            "invalid_default_workdir",
            "Default workdir does not exist.",
            400,
        ) from exc
    if not resolved.is_dir():
        raise AppError(
            "invalid_default_workdir",
            "Default workdir must be a directory.",
            400,
        )
    return str(resolved)
