from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, Field

from capabilities import CapabilityKind, CapabilityMetadata

from domain.models import AccountConnection, Approval, Conversation, ConversationMessage, Task
from agent.skill_management.lifecycle import SkillInventoryItem

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
    conversation_id: str | None = None


class TaskResponse(BaseModel):
    task_id: str
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
    items: list[TaskResponse]


class TaskSubmissionResponse(BaseModel):
    task: TaskResponse
    queued: bool


class ConversationCreateRequest(BaseModel):
    user_id: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=255)


class ConversationActorRequest(BaseModel):
    user_id: str = Field(min_length=1)


class ConversationResponse(BaseModel):
    conversation_id: str
    user_id: str
    title: str
    channel: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]


class ConversationMessageResponse(BaseModel):
    message_id: str
    conversation_id: str
    task_id: str | None
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationMessageListResponse(BaseModel):
    items: list[ConversationMessageResponse]
    compacted: bool = False
    summary_updated_at: datetime | None = None
    summary_version: str | None = None


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


class AccountConnectionCreateRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=36)
    provider: Literal["smtp", "caldav", "browser"]
    display_name: str = Field(min_length=1, max_length=255)
    credentials: dict[str, str]


class AccountConnectionActorRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=36)


class AccountConnectionResponse(BaseModel):
    connection_id: str
    user_id: str
    provider: str
    display_name: str
    status: str
    last_checked_at: datetime | None
    last_error_code: str | None


class AccountConnectionListResponse(BaseModel):
    items: list[AccountConnectionResponse]


class KnowledgeImportResponse(BaseModel):
    document_id: str
    source_label: str
    status: str
    chunk_count: int
    unchanged: bool


class KnowledgeDocumentResponse(BaseModel):
    document_id: str
    source_label: str
    media_type: str
    status: str
    chunk_count: int
    last_error_code: str | None


class KnowledgeDocumentListResponse(BaseModel):
    items: list[KnowledgeDocumentResponse]


class KnowledgeSearchItem(BaseModel):
    document_id: str
    source_label: str
    ordinal: int
    content: str
    score: int


class KnowledgeSearchResponse(BaseModel):
    items: list[KnowledgeSearchItem]


class ReminderCreateRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=10_000)
    due_at: datetime
    channel: Literal["desktop", "langbot"]


class ReminderActorRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=36)


class ReminderResponse(BaseModel):
    reminder_id: str
    user_id: str
    title: str
    message: str
    due_at: datetime
    channel: str
    status: str
    cancelled_at: datetime | None
    delivery_status: str | None = None
    last_error_code: str | None = None


class ReminderListResponse(BaseModel):
    items: list[ReminderResponse]


class DesktopNotificationResponse(BaseModel):
    outbox_id: str
    reminder_id: str
    title: str
    message: str
    due_at: datetime


class DesktopNotificationListResponse(BaseModel):
    items: list[DesktopNotificationResponse]


class ApprovalResponse(BaseModel):
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
        conversation_id=task.conversation_id,
        result_text=task.result_text,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def conversation_response(item: Conversation) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=item.id,
        user_id=item.user_id,
        title=item.title,
        channel=item.channel,
        archived_at=item.archived_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def conversation_message_response(
    item: ConversationMessage,
) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        message_id=item.id,
        conversation_id=item.conversation_id,
        task_id=item.task_id,
        role=cast(Literal["user", "assistant"], item.role),
        content=item.content,
        created_at=item.created_at,
    )


def account_connection_response(item: AccountConnection) -> AccountConnectionResponse:
    return AccountConnectionResponse(
        connection_id=item.id,
        user_id=item.user_id,
        provider=item.provider,
        display_name=item.display_name,
        status=item.status,
        last_checked_at=item.last_checked_at,
        last_error_code=item.last_error_code,
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
