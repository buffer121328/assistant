from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from packages.capabilities import CapabilityKind, CapabilityMetadata

from .models import Approval, Task
from .skill_lifecycle import SkillInventoryItem

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


class LangBotConversation(BaseModel):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)


class LangBotSender(BaseModel):
    id: str = Field(min_length=1)


class LangBotMessage(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1)


class LangBotWebhookRequest(BaseModel):
    message_id: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    conversation: LangBotConversation
    sender: LangBotSender
    message: LangBotMessage


class TaskCreateRequest(BaseModel):
    user_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    input_text: str = Field(min_length=1)
    workflow_key: str | None = None
    model_class: Literal["light", "standard"] | None = None


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


class TaskSubmissionResponse(BaseModel):
    task: TaskResponse
    queued: bool


class CapabilityResponse(BaseModel):
    id: str
    kind: CapabilityKind
    display_name: str
    summary: str
    source: str
    enabled: bool
    risk_level: Literal["L1", "L2", "L3"]
    requires_approval: bool


class CapabilityCatalogResponse(BaseModel):
    revision: int
    items: list[CapabilityResponse]


class SkillResponse(BaseModel):
    name: str
    display_name: str
    summary: str
    version: str
    source: Literal["builtin", "managed"]
    enabled: bool
    manageable: bool


class SkillListResponse(BaseModel):
    items: list[SkillResponse]


class SkillCreateRequest(BaseModel):
    user_id: str = Field(min_length=1)
    name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    )
    display_name: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    instructions: str = Field(min_length=1, max_length=131072)


class SkillActorRequest(BaseModel):
    user_id: str = Field(min_length=1)


class ApprovalResponse(BaseModel):
    approval_id: str
    task_id: str
    tool_name: str
    status: str
    decided_by_user_id: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse]


class ApprovalDecisionRequest(BaseModel):
    user_id: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]


class ApprovalDecisionResponse(BaseModel):
    approval: ApprovalResponse
    task: TaskResponse
    queued: bool


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


def capability_response(metadata: CapabilityMetadata) -> CapabilityResponse:
    return CapabilityResponse(
        id=metadata.id,
        kind=metadata.kind,
        display_name=metadata.display_name,
        summary=metadata.summary,
        source=metadata.source,
        enabled=metadata.enabled,
        risk_level=metadata.risk_level,
        requires_approval=metadata.requires_approval,
    )


def skill_response(item: SkillInventoryItem) -> SkillResponse:
    return SkillResponse(
        name=item.name,
        display_name=item.display_name,
        summary=item.summary,
        version=item.version,
        source=item.source,
        enabled=item.enabled,
        manageable=item.manageable,
    )


def approval_response(approval: Approval) -> ApprovalResponse:
    return ApprovalResponse(
        approval_id=approval.id,
        task_id=approval.task_id,
        tool_name=approval.tool_name,
        status=approval.status,
        decided_by_user_id=approval.decided_by_user_id,
        decided_at=approval.decided_at,
        created_at=approval.created_at,
        updated_at=approval.updated_at,
    )
