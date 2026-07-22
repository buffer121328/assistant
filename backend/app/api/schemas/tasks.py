from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, Field

from domain.models import Approval, Task


class TaskCreateRequest(BaseModel):
    """表示 处理 task create request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    input_text: str = Field(min_length=1)
    workflow_key: str | None = None
    model_class: Literal["light", "standard"] | None = None
    conversation_id: str | None = None


class TaskResponse(BaseModel):
    """表示 处理 task response 的后端数据结构或服务对象。"""

    task_id: str
    trace_id: str
    user_id: str
    platform: str
    task_type: str
    input_text: str
    status: str
    workflow_key: str | None
    model_class: str | None
    conversation_id: str | None
    result_text: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    """表示 处理 task list response 的后端数据结构或服务对象。"""

    items: list[TaskResponse]


class TaskSubmissionResponse(BaseModel):
    """表示 处理 task submission response 的后端数据结构或服务对象。"""

    task: TaskResponse
    queued: bool


class ApprovalResponse(BaseModel):
    """表示 处理 approval response 的后端数据结构或服务对象。"""

    approval_id: str
    task_id: str
    tool_name: str
    approval_type: Literal["tool", "plan", "review"]
    subject: str
    request_summary: str | None
    status: str
    decided_by_user_id: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalListResponse(BaseModel):
    """表示 处理 approval list response 的后端数据结构或服务对象。"""

    items: list[ApprovalResponse]


class ApprovalDecisionRequest(BaseModel):
    """表示 处理 approval decision request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]


class ApprovalDecisionResponse(BaseModel):
    """表示 处理 approval decision response 的后端数据结构或服务对象。"""

    approval: ApprovalResponse
    task: TaskResponse
    queued: bool


def task_response(task: Task) -> TaskResponse:
    """处理 task response。

    Args:
        task: task 参数。
    """
    return TaskResponse(
        task_id=task.id,
        trace_id=task.id,
        user_id=task.user_id,
        platform=task.platform,
        task_type=task.task_type,
        input_text=task.input_text,
        status=task.status,
        workflow_key=task.workflow_key,
        model_class=task.model_class,
        conversation_id=task.conversation_id,
        result_text=task.result_text,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def approval_response(approval: Approval) -> ApprovalResponse:
    """处理 approval response。

    Args:
        approval: approval 参数。
    """
    return ApprovalResponse(
        approval_id=approval.id,
        task_id=approval.task_id,
        tool_name=approval.tool_name,
        approval_type=cast(
            Literal["tool", "plan", "review"],
            approval.approval_type,
        ),
        subject=approval.subject,
        request_summary=approval.request_summary,
        status=approval.status,
        decided_by_user_id=approval.decided_by_user_id,
        decided_at=approval.decided_at,
        created_at=approval.created_at,
        updated_at=approval.updated_at,
    )
