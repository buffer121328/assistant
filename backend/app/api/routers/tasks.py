from __future__ import annotations

import asyncio
import json
import sys
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.conversations import ConversationError
from infrastructure.database import get_session
from app.support.errors import AppError
from domain.models import (
    Approval,
    ApprovalStatus,
    MemoryRetrievalTrace,
    MemoryRetrievalTraceItem,
    ModelLog,
    Task,
    TaskStatus,
    ToolLog,
)
from models import sanitize_text
from app.api.schemas import (
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
from domain.services import ApprovalService, TaskService, TaskServiceError
from domain.task_events import TaskEventRepository, event_record
from workers.worker import enqueue_task_execution as _default_enqueue_task_execution

router = APIRouter()


def _enqueue_task_execution(task_id: str, *, runtime_settings: Any = None) -> bool:
    """执行 处理 enqueue task execution 的内部辅助逻辑。

    Args:
        task_id: task_id 参数。
        runtime_settings: runtime_settings 参数。
    """
    routes_module = sys.modules.get("app.api.router")
    enqueue = getattr(routes_module, "enqueue_task_execution", None)
    if enqueue is None:
        enqueue = _default_enqueue_task_execution
    return bool(enqueue(task_id, runtime_settings=runtime_settings))


def raise_app_error(exc: TaskServiceError) -> None:
    """处理 raise app error。

    Args:
        exc: exc 参数。
    """
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
    """创建 task。

    Args:
        payload: payload 参数。
        session: session 参数。
    """
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
    """处理 submit task。

    Args:
        payload: payload 参数。
        request: request 参数。
        session: session 参数。
    """
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
    """列出 tasks。

    Args:
        user_id: user_id 参数。
        session: session 参数。
    """
    try:
        tasks = await TaskService(session).list_tasks(user_id)
    except TaskServiceError as exc:
        raise_app_error(exc)
    return TaskListResponse(items=[task_response(task) for task in tasks])


@router.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResponse:
    """获取 task。

    Args:
        task_id: task_id 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
    try:
        task = await TaskService(session).get_task_by_user(
            task_id=task_id,
            user_id=user_id,
        )
    except TaskServiceError as exc:
        raise_app_error(exc)
    return task_response(task)


@router.get("/api/tasks/{task_id}/diagnostics")
async def get_task_diagnostics(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """获取 task diagnostics。

    Args:
        task_id: task_id 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    )
    if task is None:
        raise AppError("task_not_found", "Task not found.", 404)

    events = await TaskEventRepository(session).list_after(task_id=task_id, after=0)
    model_logs = list(
        await session.scalars(
            select(ModelLog)
            .where(ModelLog.task_id == task_id)
            .order_by(ModelLog.created_at, ModelLog.id)
        )
    )
    tool_logs = list(
        await session.scalars(
            select(ToolLog)
            .where(ToolLog.task_id == task_id)
            .order_by(ToolLog.created_at, ToolLog.id)
        )
    )
    approvals = list(
        await session.scalars(
            select(Approval)
            .where(Approval.task_id == task_id)
            .order_by(Approval.created_at, Approval.id)
        )
    )
    retrieval = await session.scalar(
        select(MemoryRetrievalTrace)
        .where(
            MemoryRetrievalTrace.task_id == task_id,
            MemoryRetrievalTrace.user_id == user_id,
        )
        .order_by(MemoryRetrievalTrace.created_at.desc())
        .limit(1)
    )
    retrieval_items: list[MemoryRetrievalTraceItem] = []
    if retrieval is not None:
        retrieval_items = list(
            await session.scalars(
                select(MemoryRetrievalTraceItem)
                .where(MemoryRetrievalTraceItem.trace_id == retrieval.id)
                .order_by(
                    MemoryRetrievalTraceItem.final_rank.asc().nulls_last(),
                    MemoryRetrievalTraceItem.id,
                )
            )
        )

    return {
        "trace_id": task.id,
        "task": task_response(task).model_dump(mode="json"),
        "events": [event_record(item) for item in events],
        "model_calls": [
            {
                "model_log_id": item.id,
                "model_class": item.model_class,
                "response_summary": _diagnostic_summary(item.response_text),
                "error_summary": _diagnostic_summary(item.error_message),
                "created_at": item.created_at.isoformat(),
            }
            for item in model_logs
        ],
        "tool_calls": [
            {
                "tool_log_id": item.id,
                "tool_name": item.tool_name,
                "status": item.status,
                "output_summary": _diagnostic_summary(item.output_text),
                "error_summary": _diagnostic_summary(item.error_message),
                "created_at": item.created_at.isoformat(),
            }
            for item in tool_logs
        ],
        "approvals": [
            {
                "approval_id": item.id,
                "approval_type": item.approval_type,
                "subject": item.subject,
                "tool_name": item.tool_name,
                "status": item.status,
                "request_summary": _diagnostic_summary(item.request_summary),
            }
            for item in approvals
        ],
        "retrieval": (
            None
            if retrieval is None
            else {
                "retrieval_trace_id": retrieval.id,
                "mode": retrieval.retrieval_mode,
                "time_intent": retrieval.time_intent,
                "candidate_count": retrieval.candidate_count,
                "injected_count": retrieval.injected_count,
                "injected_tokens": retrieval.injected_tokens,
                "sources": [
                    {
                        "source_id": f"memory:{item.memory_id}",
                        "memory_id": item.memory_id,
                        "filter_reason": item.filter_reason,
                        "final_rank": item.final_rank,
                        "injected_tokens": item.injected_tokens,
                    }
                    for item in retrieval_items
                ],
            }
        ),
        "error_summary": _diagnostic_summary(task.error_message),
    }


def _diagnostic_summary(value: str | None, *, limit: int = 1000) -> str | None:
    """执行 处理 diagnostic summary 的内部辅助逻辑。

    Args:
        value: value 参数。
        limit: limit 参数。
    """
    if value is None:
        return None
    return sanitize_text(value)[:limit]


@router.get("/api/tasks/{task_id}/events/stream")
async def stream_task_events(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """处理 stream task events。

    Args:
        task_id: task_id 参数。
        user_id: user_id 参数。
        request: request 参数。
        session: session 参数。
        after: after 参数。
    """
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    )
    if task is None:
        raise AppError("task_not_found", "Task not found.", 404)

    async def records():
        """处理 records。"""
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
    """列出 task approvals。

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
    """处理 decide task approval。

    Args:
        task_id: task_id 参数。
        approval_id: approval_id 参数。
        payload: payload 参数。
        request: request 参数。
        session: session 参数。
    """
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
