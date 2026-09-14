from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AdminOrganizationCreateRequest(BaseModel):
    """AdminOrganizationCreateRequest 的接口数据模型。"""
    name: str
    type: str = "department"
    parent_id: str | None = None


class AdminConnectorCreateRequest(BaseModel):
    """AdminConnectorCreateRequest 的接口数据模型。"""
    key: str
    connector_type: str
    display_name: str
    config: dict[str, object] = {}


class AdminConnectorInstanceCreateRequest(BaseModel):
    """AdminConnectorInstanceCreateRequest 的接口数据模型。"""
    organization_id: str
    auth_mode: str = "governed"
    credential_ref: str | None = None


class AdminConnectorToolReviewRequest(BaseModel):
    """AdminConnectorToolReviewRequest 的接口数据模型。"""
    internal_tool_key: str
    risk_level: str
    enabled: bool = True


class AdminCapabilityDistributionRequest(BaseModel):
    """AdminCapabilityDistributionRequest 的接口数据模型。"""
    organization_id: str
    can_delegate: bool = False


class AdminMemberProvisionRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    display_name: str = Field(min_length=1, max_length=120)
    login_name: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=128)
    organization_id: str = Field(min_length=1, max_length=36)
    role: str = Field(min_length=1, max_length=64)


class AdminMemberRoleAssignmentRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    organization_id: str = Field(min_length=1, max_length=36)
    role: str = Field(min_length=1, max_length=64)


class AdminMarketplaceSkillCandidateRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    )
    display_name: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)


class NodeEnrollmentCreateRequest(BaseModel):
    """Bounded administrator request for one department enrollment."""

    organization_id: str = Field(min_length=1, max_length=36)
    expires_in_minutes: int = Field(default=60, ge=5, le=1_440)


class NodeEnrollmentStateResponse(BaseModel):
    """Safe enrollment lifecycle metadata without its secret digest."""

    id: str
    organization_id: str
    status: str
    expires_at: datetime


class NodeEnrollmentResponse(NodeEnrollmentStateResponse):
    """One-time enrollment response whose secret is never listed again."""

    enrollment_token: str


class NodeRegistrationRequest(BaseModel):
    """Node registration input guarded by a one-time enrollment."""

    enrollment_token: str = Field(min_length=10, max_length=256)
    name: str = Field(min_length=1, max_length=120)
    agent_version: str = Field(min_length=1, max_length=64)


class NodeRegistrationResponse(BaseModel):
    """Registered safe node facts plus a node credential returned once."""

    node: dict[str, object]
    node_token: str


class NodeHeartbeatRequest(BaseModel):
    """Bounded health and configuration facts reported by one node."""

    agent_version: str = Field(min_length=1, max_length=64)
    applied_config_revision: int = Field(ge=0)
    accepts_new_work: bool = True
    health_status: Literal["healthy", "degraded", "error", "unknown"] = "unknown"
    health_summary: str = Field(default="", max_length=500)


class DepartmentNodeResponse(BaseModel):
    """Safe node inventory and heartbeat facts without credential material."""

    id: str
    organization_id: str
    organization_name: str | None = None
    name: str
    status: str
    connection_state: str
    agent_version: str
    desired_config_revision: int
    applied_config_revision: int
    configuration_drift: bool
    accepts_new_work: bool
    health_status: str
    health_summary: str
    last_seen_at: datetime | None = None


class DepartmentNodeListResponse(BaseModel):
    """Bounded node inventory collection."""

    items: list[DepartmentNodeResponse]


class NodeConfigAckRequest(BaseModel):
    """One desired-configuration application result."""

    revision: int = Field(ge=1)
    status: Literal["applied", "failed"]
    error_code: str | None = Field(default=None, max_length=64)
    error_summary: str = Field(default="", max_length=500)


class NodeConfigurationResponse(BaseModel):
    """Canonical desired configuration for one authenticated node."""

    revision: int
    checksum: str
    configuration: dict[str, object]


class NodeConfigAckResponse(BaseModel):
    """Safe configuration acknowledgement result."""

    node_id: str
    revision: int
    status: str
    applied_config_revision: int
    error_code: str | None = None
    error_summary: str


class DiagnosticQuery(BaseModel):
    """Shared bounded diagnostic filter contract."""

    start_at: datetime
    end_at: datetime
    organization_id: str | None = Field(default=None, max_length=36)
    node_id: str | None = Field(default=None, max_length=36)
    task_id: str | None = Field(default=None, max_length=36)
    status: str | None = Field(default=None, max_length=32)
    capability_version: str | None = Field(default=None, max_length=128)


class DiagnosticPackageCreateRequest(DiagnosticQuery):
    """Diagnostic filter plus bounded package lifetime."""

    expires_in_minutes: int = Field(default=60, ge=5, le=1_440)


class DiagnosticRecordResponse(BaseModel):
    """Minimal redacted task diagnostic projection."""

    task_id: str
    organization_id: str | None = None
    node_id: str | None = None
    status: str
    task_type: str
    capability_versions: list[str]
    error_code: str | None = None
    error_summary: str
    created_at: datetime
    updated_at: datetime


class DiagnosticRecordListResponse(BaseModel):
    """Bounded diagnostic result collection."""

    items: list[DiagnosticRecordResponse]


class DiagnosticPackageResponse(BaseModel):
    """Safe package metadata without stored manifest content."""

    id: str
    organization_id: str | None = None
    node_id: str | None = None
    status: str
    checksum: str
    record_count: int
    expires_at: datetime
    created_at: datetime
    downloaded_at: datetime | None = None


class DiagnosticPackageListResponse(BaseModel):
    """Bounded diagnostic package metadata collection."""

    items: list[DiagnosticPackageResponse]


class NodeOperationCreateRequest(BaseModel):
    """Whitelisted node action without arbitrary command text or arguments."""

    operation_type: Literal[
        "refresh_config",
        "resync_config",
        "pause_new_work",
        "resume_new_work",
        "stop_task",
        "restart_agent",
    ]
    target_task_id: str | None = Field(default=None, max_length=36)
    expires_in_minutes: int = Field(default=10, ge=1, le=60)


class NodeOperationAckRequest(BaseModel):
    """Bounded terminal result reported by the target node."""

    status: Literal["succeeded", "failed"]
    result_code: str = Field(min_length=1, max_length=64)
    result_summary: str = Field(default="", max_length=500)


class NodeOperationResponse(BaseModel):
    """Safe finite remote-operation ledger record."""

    id: str
    organization_id: str
    node_id: str
    operation_type: str
    target_task_id: str | None = None
    status: str
    expires_at: datetime
    delivered_at: datetime | None = None
    completed_at: datetime | None = None
    result_code: str | None = None
    result_summary: str
    created_at: datetime


class NodeOperationListResponse(BaseModel):
    """Bounded finite-operation collection."""

    items: list[NodeOperationResponse]


class CapabilityRequestCreateRequest(BaseModel):
    """Bounded employee input; identity and organization come from the session."""

    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2_000)
    related_task_id: str | None = Field(default=None, min_length=1, max_length=36)


class CapabilityRequestStatusUpdateRequest(BaseModel):
    """One server-validated lifecycle target."""

    status: str = Field(min_length=1, max_length=32)


class CapabilityRequestResponse(BaseModel):
    """Safe request facts shared with an employee or authorized IT role."""

    id: str
    title: str
    description: str
    status: str
    organization_name: str
    related_task_id: str | None = None
    requester_display_name: str | None = None
    created_at: datetime
    updated_at: datetime


class CapabilityRequestListResponse(BaseModel):
    """Bounded request collection."""

    items: list[CapabilityRequestResponse]


class EffectiveCapabilityResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    id: str
    display_name: str
    summary: str


class AuthorizedSkillResponse(BaseModel):
    name: str
    display_name: str
    source: str
    status: str = "authorized"
    selectable: bool = True


class MeResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    tenant_id: str
    user_id: str
    organization_ids: list[str]
    organization_names: list[str] = []
    roles: list[str]
    authority_revision: int


class MyCapabilitiesResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    schema_version: str
    authority_revision: int
    capabilities: list[str]
    capability_details: list[EffectiveCapabilityResponse] = []
    tools: list[str]
    knowledge_scopes: list[str]
    memory_access: list[list[str]]
    skills: list[AuthorizedSkillResponse] = []


class AdminListResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    items: list[dict[str, object]]
