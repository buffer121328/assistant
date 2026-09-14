from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.tasks import raise_app_error
from app.api.schemas import ApprovalListResponse, approval_response, task_response
from application.lifecycle_hooks import build_builtin_lifecycle_registry
from application.task_execution.lifecycle import ApprovalService, TaskServiceError
from channels.desktop.local.schemas import (
    LocalApprovalDecisionRequest,
    LocalApprovalDecisionResponse,
)
from channels.desktop.local.services import safe_enqueue_task_execution
from domain.models import ApprovalStatus
from infrastructure.persistence.database import get_session

router = APIRouter()


@router.get(
    "/tasks/{task_id}/approvals",
    response_model=ApprovalListResponse,
)
async def local_list_task_approvals(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalListResponse:
    """List pending and historical approvals for a local task owner.

    Args:
        task_id: 目标任务 ID。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
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
    """Approve or reject a local approval and resume execution when approved.

    Args:
        task_id: 目标任务 ID。
        approval_id: 用于执行当前操作的 approval id 参数。
        payload: 当前操作的结构化载荷。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
    """
    decision = (
        ApprovalStatus.APPROVED
        if payload.decision == "approve"
        else ApprovalStatus.REJECTED
    )
    try:
        result = await ApprovalService(
            session,
            lifecycle_registry=build_builtin_lifecycle_registry(
                request.app.state.db_sessionmaker,
                session=session,
            ),
        ).decide(
            task_id=task_id,
            approval_id=approval_id,
            user_id=payload.user_id,
            decision=decision,
        )
    except TaskServiceError as exc:
        raise_app_error(exc)

    queued = False
    if result.changed and result.approval.status == ApprovalStatus.APPROVED.value:
        queued = safe_enqueue_task_execution(
            result.task.id,
            runtime_settings=request.app.state.settings,
            tenant_id=result.task.tenant_id,
            organization_ids=(result.task.organization_id,) if result.task.organization_id else (),
        )
    return LocalApprovalDecisionResponse(
        approval=approval_response(result.approval),
        task=task_response(result.task),
        queued=queued,
    )
