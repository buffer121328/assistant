from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.api.schemas import ApprovalResponse, TaskResponse


class LocalTaskCreateRequest(BaseModel):
    """Payload for creating a local desktop task."""

    user_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    input_text: str = Field(min_length=1)
    workflow_key: str | None = None
    model_class: Literal["light", "standard"] | None = None
    conversation_id: str | None = None


class LocalTaskSubmissionResponse(BaseModel):
    """Task submission result plus local queue status."""

    task: TaskResponse
    queued: bool


class LocalEventResponse(BaseModel):
    """Sanitized local event stream item."""

    event_id: str
    task_id: str
    type: str
    created_at: str
    sequence: int
    payload: dict[str, object]


class LocalEventListResponse(BaseModel):
    """Local event list response."""

    items: list[LocalEventResponse]


class LocalConversationTokenStatsResponse(BaseModel):
    """Local conversation token usage stats."""

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
    """Payload for continuing a local task conversation."""

    user_id: str = Field(min_length=1)
    content: str = Field(min_length=1)


class LocalApprovalDecisionRequest(BaseModel):
    """Payload for deciding a local approval request."""

    user_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=1000)


class LocalApprovalDecisionResponse(BaseModel):
    """Approval decision response plus task queue status."""

    approval: ApprovalResponse
    task: TaskResponse
    queued: bool


class LocalSettingsValidationRequest(BaseModel):
    """Payload for validating local desktop settings."""

    api_base_url: str = Field(min_length=1, max_length=500)
    default_workdir: str | None = Field(default=None, max_length=2000)
    default_model_class: Literal["light", "standard"] | None = None
    approval_policy: Literal["ask", "require_high_risk", "read_only"]


class LocalSettingsValidationResponse(BaseModel):
    """Validated local desktop settings."""

    ok: bool
    settings: dict[str, object]
