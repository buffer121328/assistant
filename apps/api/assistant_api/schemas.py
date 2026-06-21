from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .models import Task

MODEL_GATEWAY_VALIDATION_ERROR = "model_gateway_validation_error"
MODEL_GATEWAY_UNSUPPORTED_MODEL = "model_gateway_unsupported_model"
MODEL_GATEWAY_TIMEOUT = "model_gateway_timeout"
MODEL_GATEWAY_PROVIDER_ERROR = "model_gateway_provider_error"


class ModelGatewayMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ModelChatRequest(BaseModel):
    user_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    model_class: str | None = None
    messages: list[ModelGatewayMessage] = Field(min_length=1)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4000, ge=1, le=32000)


class ModelGatewayUsage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelChatResponse(BaseModel):
    provider: str
    model: str
    content: str
    usage: ModelGatewayUsage
    latency_ms: int = Field(ge=0)
    status: Literal["succeeded"]


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
