from datetime import datetime

from pydantic import BaseModel, Field

from .models import Task


class TaskCreateRequest(BaseModel):
    user_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    input_text: str = Field(min_length=1)
    workflow_key: str | None = None
    model_class: str | None = None


class TaskResponse(BaseModel):
    task_id: str
    user_id: str
    platform: str
    task_type: str
    input_text: str
    status: str
    workflow_key: str | None
    model_class: str | None
    result_text: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    items: list[TaskResponse]


def task_response(task: Task) -> TaskResponse:
    return TaskResponse(
        task_id=task.id,
        user_id=task.user_id,
        platform=task.platform,
        task_type=task.task_type,
        input_text=task.input_text,
        status=task.status,
        workflow_key=task.workflow_key,
        model_class=task.model_class,
        result_text=task.result_text,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
