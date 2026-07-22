import json
from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, Field

from capabilities import CapabilityKind, CapabilityMetadata

from domain.models import (
    AccountConnection,
    Approval,
    Conversation,
    ConversationMessage,
    ProcessedMessage,
    Task,
)
from agent.skill_management.lifecycle import SkillInventoryItem

MODEL_GATEWAY_VALIDATION_ERROR = "model_gateway_validation_error"
MODEL_GATEWAY_UNSUPPORTED_MODEL = "model_gateway_unsupported_model"
MODEL_GATEWAY_TIMEOUT = "model_gateway_timeout"
MODEL_GATEWAY_PROVIDER_ERROR = "model_gateway_provider_error"


class ModelGatewayMessage(BaseModel):
    """表示 处理 model gateway message 的后端数据结构或服务对象。"""

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ModelChatRequest(BaseModel):
    """表示 处理 model chat request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    model_class: str | None = None
    messages: list[ModelGatewayMessage] = Field(min_length=1)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4000, ge=1, le=32000)


class ModelGatewayUsage(BaseModel):
    """表示 处理 model gateway usage 的后端数据结构或服务对象。"""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelChatResponse(BaseModel):
    """表示 处理 model chat response 的后端数据结构或服务对象。"""

    provider: str
    model: str
    content: str
    usage: ModelGatewayUsage
    latency_ms: int = Field(ge=0)
    status: Literal["succeeded"]


class LangBotConversation(BaseModel):
    """表示 处理 lang bot conversation 的后端数据结构或服务对象。"""

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)


class LangBotSender(BaseModel):
    """表示 处理 lang bot sender 的后端数据结构或服务对象。"""

    id: str = Field(min_length=1)


class LangBotMessage(BaseModel):
    """表示 处理 lang bot message 的后端数据结构或服务对象。"""

    type: Literal["text"]
    text: str = Field(min_length=1)


class LangBotWebhookRequest(BaseModel):
    """表示 处理 lang bot webhook request 的后端数据结构或服务对象。"""

    message_id: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    conversation: LangBotConversation
    sender: LangBotSender
    message: LangBotMessage


class RemoteControlBridgeResponseTarget(BaseModel):
    """表示 处理 remote control bridge response target 的后端数据结构或服务对象。"""

    adapter: str
    conversation_id: str
    conversation_type: str


class RemoteControlBridgeSessionResponse(BaseModel):
    """表示 处理 remote control bridge session response 的后端数据结构或服务对象。"""

    bridge_id: str
    platform: str
    message_id: str
    adapter: str | None
    sender_id: str | None
    conversation_id: str | None
    conversation_type: str | None
    message_text: str | None
    intent_outcome: str | None
    reason: str
    task_id: str | None
    task_status: str | None
    response_target: RemoteControlBridgeResponseTarget | None
    delivery_status: str | None
    delivery_attempt_count: int
    delivery_error_summary: str | None
    delivery_result_json: str | None
    created_at: datetime
    updated_at: datetime


class RemoteControlBridgeSessionListResponse(BaseModel):
    """表示 处理 remote control bridge session list response 的后端数据结构或服务对象。"""

    items: list[RemoteControlBridgeSessionResponse]


class RemoteControlBridgeReplayResponse(BaseModel):
    """表示 处理 remote control bridge replay response 的后端数据结构或服务对象。"""

    dispatch_status: str
    message: str
    session: RemoteControlBridgeSessionResponse


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


class ConversationCreateRequest(BaseModel):
    """表示 处理 conversation create request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=255)


class ConversationActorRequest(BaseModel):
    """表示 处理 conversation actor request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)


class ConversationResponse(BaseModel):
    """表示 处理 conversation response 的后端数据结构或服务对象。"""

    conversation_id: str
    user_id: str
    title: str
    channel: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    """表示 处理 conversation list response 的后端数据结构或服务对象。"""

    items: list[ConversationResponse]


class ConversationMessageResponse(BaseModel):
    """表示 处理 conversation message response 的后端数据结构或服务对象。"""

    message_id: str
    conversation_id: str
    task_id: str | None
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationMessageListResponse(BaseModel):
    """表示 处理 conversation message list response 的后端数据结构或服务对象。"""

    items: list[ConversationMessageResponse]
    compacted: bool = False
    summary_updated_at: datetime | None = None
    summary_version: str | None = None


class CapabilityResponse(BaseModel):
    """表示 处理 capability response 的后端数据结构或服务对象。"""

    id: str
    kind: CapabilityKind
    display_name: str
    summary: str
    source: str
    enabled: bool
    risk_level: Literal["L0", "L1", "L2", "L3", "L4"]
    requires_approval: bool


class CapabilityCatalogResponse(BaseModel):
    """表示 处理 capability catalog response 的后端数据结构或服务对象。"""

    revision: int
    items: list[CapabilityResponse]


class SkillResponse(BaseModel):
    """表示 处理 skill response 的后端数据结构或服务对象。"""

    name: str
    display_name: str
    summary: str
    version: str
    source: Literal["builtin", "managed"]
    enabled: bool
    manageable: bool


class SkillListResponse(BaseModel):
    """表示 处理 skill list response 的后端数据结构或服务对象。"""

    items: list[SkillResponse]


class SkillCreateRequest(BaseModel):
    """表示 处理 skill create request 的后端数据结构或服务对象。"""

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
    """表示 处理 skill actor request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)


class AccountConnectionCreateRequest(BaseModel):
    """表示 处理 account connection create request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1, max_length=36)
    provider: Literal["smtp", "caldav", "browser"]
    display_name: str = Field(min_length=1, max_length=255)
    credentials: dict[str, str]


class AccountConnectionActorRequest(BaseModel):
    """表示 处理 account connection actor request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1, max_length=36)


class AccountConnectionResponse(BaseModel):
    """表示 处理 account connection response 的后端数据结构或服务对象。"""

    connection_id: str
    user_id: str
    provider: str
    display_name: str
    status: str
    last_checked_at: datetime | None
    last_error_code: str | None


class AccountConnectionListResponse(BaseModel):
    """表示 处理 account connection list response 的后端数据结构或服务对象。"""

    items: list[AccountConnectionResponse]


class KnowledgeImportResponse(BaseModel):
    """表示 处理 knowledge import response 的后端数据结构或服务对象。"""

    document_id: str
    source_label: str
    status: str
    chunk_count: int
    unchanged: bool


class KnowledgeDocumentResponse(BaseModel):
    """表示 处理 knowledge document response 的后端数据结构或服务对象。"""

    document_id: str
    source_label: str
    media_type: str
    status: str
    chunk_count: int
    last_error_code: str | None


class KnowledgeDocumentListResponse(BaseModel):
    """表示 处理 knowledge document list response 的后端数据结构或服务对象。"""

    items: list[KnowledgeDocumentResponse]


class KnowledgeDeleteResponse(BaseModel):
    """表示 处理 knowledge delete response 的后端数据结构或服务对象。"""

    document_id: str
    status: str
    chunk_count: int


class KnowledgeSearchItem(BaseModel):
    """表示 处理 knowledge search item 的后端数据结构或服务对象。"""

    document_id: str
    source_id: str
    source_label: str
    citation: str
    citation_token: str
    ordinal: int
    content: str
    score: int
    trust_boundary: str
    instruction_risk: bool


class KnowledgeSearchResponse(BaseModel):
    """表示 处理 knowledge search response 的后端数据结构或服务对象。"""

    items: list[KnowledgeSearchItem]
    answerable: bool


class ReminderCreateRequest(BaseModel):
    """表示 处理 reminder create request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=10_000)
    due_at: datetime
    channel: Literal["desktop", "langbot"]


class ReminderActorRequest(BaseModel):
    """表示 处理 reminder actor request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1, max_length=36)


class ReminderResponse(BaseModel):
    """表示 处理 reminder response 的后端数据结构或服务对象。"""

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
    """表示 处理 reminder list response 的后端数据结构或服务对象。"""

    items: list[ReminderResponse]


class DesktopNotificationResponse(BaseModel):
    """表示 处理 desktop notification response 的后端数据结构或服务对象。"""

    outbox_id: str
    reminder_id: str
    title: str
    message: str
    due_at: datetime


class DesktopNotificationListResponse(BaseModel):
    """表示 处理 desktop notification list response 的后端数据结构或服务对象。"""

    items: list[DesktopNotificationResponse]


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


def remote_control_bridge_response(
    item: ProcessedMessage,
    *,
    task_status: str | None = None,
) -> RemoteControlBridgeSessionResponse:
    """处理 remote control bridge response。

    Args:
        item: item 参数。
        task_status: task_status 参数。
    """
    response_target: RemoteControlBridgeResponseTarget | None = None
    if item.response_target:
        try:
            target = json.loads(item.response_target)
        except json.JSONDecodeError:
            target = None
        if isinstance(target, dict):
            adapter = target.get("adapter")
            conversation_id = target.get("conversation_id")
            conversation_type = target.get("conversation_type")
            if (
                isinstance(adapter, str)
                and isinstance(conversation_id, str)
                and isinstance(conversation_type, str)
            ):
                response_target = RemoteControlBridgeResponseTarget(
                    adapter=adapter,
                    conversation_id=conversation_id,
                    conversation_type=conversation_type,
                )

    return RemoteControlBridgeSessionResponse(
        bridge_id=item.id,
        platform=item.platform,
        message_id=item.message_id,
        adapter=item.adapter,
        sender_id=item.sender_id,
        conversation_id=item.chat_id,
        conversation_type=item.conversation_type,
        message_text=item.message_text,
        intent_outcome=item.intent_outcome,
        reason=item.reason,
        task_id=item.task_id,
        task_status=task_status,
        response_target=response_target,
        delivery_status=item.delivery_status,
        delivery_attempt_count=item.delivery_attempt_count,
        delivery_error_summary=item.delivery_error_summary,
        delivery_result_json=item.delivery_result_json,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def conversation_response(item: Conversation) -> ConversationResponse:
    """处理 conversation response。

    Args:
        item: item 参数。
    """
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
    """处理 conversation message response。

    Args:
        item: item 参数。
    """
    return ConversationMessageResponse(
        message_id=item.id,
        conversation_id=item.conversation_id,
        task_id=item.task_id,
        role=cast(Literal["user", "assistant"], item.role),
        content=item.content,
        created_at=item.created_at,
    )


def account_connection_response(item: AccountConnection) -> AccountConnectionResponse:
    """处理 account connection response。

    Args:
        item: item 参数。
    """
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
    """处理 capability response。

    Args:
        metadata: metadata 参数。
    """
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
    """处理 skill response。

    Args:
        item: item 参数。
    """
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
