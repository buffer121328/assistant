from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.api.schemas import ApprovalResponse, TaskResponse


class LocalCommandDescriptorResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    command_id: str
    label: str
    description: str
    selectable: bool


class LocalCommandCatalogResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    items: list[LocalCommandDescriptorResponse]


class LocalTaskCreateRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    command_id: str | None = Field(default=None, min_length=1, max_length=64)
    input_text: str = Field(min_length=1)
    workflow_key: str | None = None
    model_class: Literal["light", "standard"] | None = None
    conversation_id: str | None = None
    space_id: str | None = Field(default=None, min_length=1, max_length=36)
    resource_reference_ids: list[str] = Field(default_factory=list, max_length=32)


class LocalTaskSubmissionResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    task: TaskResponse
    queued: bool


class LocalTaskContextResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    context_snapshot_id: str
    task_id: str
    conversation_id: str | None
    user_id: str
    tenant_id: str
    organization_id: str | None
    owner_type: str
    owner_id: str
    visibility: str
    command_id: str | None
    state: Literal["initial", "finalized"]
    resource_reference_ids: list[str]
    resolved_resource_versions: dict[str, str]
    memory_scope_snapshot: list[str]
    knowledge_scope_snapshot: list[str]
    capability_snapshot: list[str]
    agent_profile_schema_version: str | None
    created_at: datetime
    finalized_at: datetime | None


class LocalEventResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    event_id: str
    task_id: str
    type: str
    normalized_type: str | None = None
    run_id: str | None = None
    created_at: str
    sequence: int
    payload: dict[str, object]


class LocalEventListResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    items: list[LocalEventResponse]


class LocalConversationTokenStatsResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    conversation_id: str
    message_count: int
    user_message_count: int
    assistant_message_count: int
    total_estimated_tokens: int
    user_estimated_tokens: int
    assistant_estimated_tokens: int
    token_limit: int
    used_input_tokens: int
    used_output_tokens: int
    used_total_tokens: int
    reserved_input_tokens: int
    reserved_output_tokens: int
    reserved_total_tokens: int
    remaining_tokens: int
    available_tokens: int
    blocked_reason: str | None
    usage_ratio: float
    status: Literal["ok", "warning", "full"]


class LocalMessageAppendRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    command_id: str | None = Field(default=None, min_length=1, max_length=64)
    resource_reference_ids: list[str] = Field(default_factory=list, max_length=32)


class LocalConversationActorRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1)


class LocalConversationSpaceUpdateRequest(LocalConversationActorRequest):
    """定义当前接口使用的数据模型。"""

    space_id: str | None = Field(default=None, min_length=1, max_length=36)


class LocalConversationWorkSummaryResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    conversation_id: str
    scope_kind: Literal["personal", "space"]
    scope_name: str
    space_id: str | None
    context_count: int
    artifact_count: int
    pending_approval_count: int


class LocalResourceReferenceCreateRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1)
    resource_kind: Literal["local_file"]
    source_ref: str = Field(min_length=1, max_length=4096)
    sensitivity: Literal["normal", "sensitive", "restricted"] = "normal"
    pinned: bool = False


class LocalArtifactResourceReferenceCreateRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1, max_length=64)
    sensitivity: Literal["normal", "sensitive", "restricted"] = "normal"
    pinned: bool = False


class LocalExternalRecordResourceReferenceCreateRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1)
    provider: str = Field(min_length=1, max_length=64)
    external_resource_id: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=255)
    version: str | None = Field(default=None, max_length=512)
    sensitivity: Literal["normal", "sensitive", "restricted"] = "normal"
    pinned: bool = False


class LocalResourceReferenceResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    reference_id: str
    conversation_id: str
    resource_kind: str
    provider: str
    display_name: str
    version: str
    content_hash: str | None
    size_bytes: int
    user_id: str
    tenant_id: str
    organization_id: str | None
    owner_type: str
    owner_id: str
    visibility: str
    sensitivity: str
    status: str
    pinned: bool
    created_at: datetime
    updated_at: datetime
    last_resolved_at: datetime | None


class LocalResourceReferenceListResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    items: list[LocalResourceReferenceResponse]


class LocalArtifactResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    artifact_id: str
    tenant_id: str
    organization_id: str | None
    owner_type: str
    owner_id: str
    conversation_id: str | None
    task_id: str
    run_id: str | None
    filename: str
    media_type: str
    size_bytes: int
    content_hash: str
    version: str
    generation_method: str
    source_reference_versions: dict[str, str]
    visibility: str
    sensitivity: str
    lifecycle_state: str
    publication_state: str
    retention_state: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    revoked_at: datetime | None


class LocalArtifactListResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    items: list[LocalArtifactResponse]


class LocalArtifactActionRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1)


class LocalApprovalDecisionRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=1000)


class LocalApprovalDecisionResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    approval: ApprovalResponse
    task: TaskResponse
    queued: bool


class LocalSettingsValidationRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    api_base_url: str = Field(min_length=1, max_length=500)
    default_workdir: str | None = Field(default=None, max_length=2000)
    default_model_class: Literal["light", "standard"] | None = None
    approval_policy: Literal["ask", "require_high_risk", "read_only"]


class LocalSettingsValidationResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    ok: bool
    settings: dict[str, object]


class LocalWorkspaceFileResponse(BaseModel):
    """One safe user-facing file listed for the current private Workspace."""

    file_id: str
    source_kind: Literal["uploaded", "generated"]
    display_name: str
    media_type: str | None
    size_bytes: int
    conversation_id: str
    conversation_title: str
    created_at: datetime
    updated_at: datetime


class LocalWorkspaceFileListResponse(BaseModel):
    """Bounded safe file collection for one private Workspace."""

    items: list[LocalWorkspaceFileResponse]
