from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_id


class Tenant(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    authority_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class GovernanceAudit(Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "governance_audits"
    __table_args__ = (
        Index("ix_governance_audit_tenant_created", "tenant_id", "created_at"),
        Index("ix_governance_audit_task_run", "task_id", "run_id"),
        Index(
            "ix_governance_audit_conversation_resource",
            "conversation_id",
            "resource_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(String(36))
    conversation_id: Mapped[str | None] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    run_id: Mapped[str | None] = mapped_column(String(36))
    agent_id: Mapped[str | None] = mapped_column(String(128))
    capability_key: Mapped[str | None] = mapped_column(String(128))
    tool_key: Mapped[str | None] = mapped_column(String(128))
    provider_key: Mapped[str | None] = mapped_column(String(128))
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(255))
    risk: Mapped[str | None] = mapped_column(String(8))
    policy_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(36))
    arguments_hash: Mapped[str | None] = mapped_column(String(64))
    result_status: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(128))
    cost_usd: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Organization(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "parent_id", "name", name="uq_org_parent_name"),
        Index("ix_organizations_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), default="department", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class OrganizationMembership(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "organization_id", "user_id", "role", name="uq_org_membership_role"
        ),
        Index("ix_org_memberships_user_status", "tenant_id", "user_id", "status"),
        CheckConstraint(
            "role IN ('member','manager','department_admin','enterprise_admin','capability_publisher','connector_admin','auditor')",
            name="ck_org_membership_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class PolicyRule(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "policy_rules"
    __table_args__ = (
        Index("ix_policy_rules_tenant_action_status", "tenant_id", "action", "status"),
        CheckConstraint(
            "effect IN ('ALLOW','DENY','CONFIRM','APPROVAL')",
            name="ck_policy_rule_effect",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), default="*", nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
    constraints_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class CapabilityDefinition(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "capability_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", "version", name="uq_capability_key_version"),
        Index("ix_capability_definitions_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    bindings_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class CapabilityGrant(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "capability_grants"
    __table_args__ = (
        Index("ix_capability_grants_target_status", "tenant_id", "target_organization_id", "status"),
        CheckConstraint("max_delegate_depth >= 0", name="ck_capability_grant_depth"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    source_organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    target_organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    capability_id: Mapped[str] = mapped_column(
        ForeignKey("capability_definitions.id"), nullable=False
    )
    version_constraint: Mapped[str] = mapped_column(String(64), nullable=False)
    can_delegate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_delegate_depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    constraints_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class CapabilityRequest(TimestampMixin, Base):
    """A governed employee request for a missing work capability."""

    __tablename__ = "capability_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "requester_id", "idempotency_key",
            name="uq_capability_request_idempotency",
        ),
        Index("ix_capability_requests_requester_created", "requester_id", "created_at"),
        Index("ix_capability_requests_tenant_status_created", "tenant_id", "status", "created_at"),
        CheckConstraint(
            "status IN ('pending','communicating','configuring','testing','pending_approval','published','rejected','closed')",
            name="ck_capability_request_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    related_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)


class NodeEnrollment(TimestampMixin, Base):
    """Single-use secret digest authorizing one department-node registration."""

    __tablename__ = "node_enrollments"
    __table_args__ = (
        Index("ix_node_enrollments_tenant_org_status", "tenant_id", "organization_id", "status"),
        CheckConstraint("status IN ('active','used','revoked')", name="ck_node_enrollment_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    secret_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DepartmentNode(TimestampMixin, Base):
    """Revocable department-bound identity and latest bounded runtime state."""

    __tablename__ = "department_nodes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "organization_id", "name", name="uq_department_node_name"),
        Index("ix_department_nodes_tenant_org_status", "tenant_id", "organization_id", "status"),
        CheckConstraint("status IN ('active','revoked')", name="ck_department_node_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    credential_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    desired_config_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    applied_config_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accepts_new_work: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    health_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    health_summary: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NodeConfigSnapshot(Base):
    """Immutable canonical configuration projected for one organization."""

    __tablename__ = "node_config_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "organization_id", "revision", name="uq_node_config_revision"),
        UniqueConstraint("tenant_id", "organization_id", "checksum", name="uq_node_config_checksum"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NodeConfigApplication(Base):
    """One node acknowledgement for a desired configuration revision."""

    __tablename__ = "node_config_applications"
    __table_args__ = (
        UniqueConstraint("node_id", "revision", name="uq_node_config_application_revision"),
        CheckConstraint("status IN ('applied','failed')", name="ck_node_config_application_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(ForeignKey("department_nodes.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DiagnosticPackage(Base):
    """Immutable expiring bundle of redacted operational diagnostic facts."""

    __tablename__ = "diagnostic_packages"
    __table_args__ = (Index("ix_diagnostic_packages_tenant_expires", "tenant_id", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"))
    node_id: Mapped[str | None] = mapped_column(ForeignKey("department_nodes.id"))
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    filters_json: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ready", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NodeRemoteOperation(TimestampMixin, Base):
    """Idempotent expiring whitelist operation delivered to exactly one node."""

    __tablename__ = "node_remote_operations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "requested_by", "idempotency_key", name="uq_node_operation_idempotency"),
        Index("ix_node_operations_node_status_expires", "node_id", "status", "expires_at"),
        CheckConstraint(
            "operation_type IN ('refresh_config','resync_config','pause_new_work','resume_new_work','stop_task','restart_agent')",
            name="ck_node_operation_type",
        ),
        CheckConstraint(
            "status IN ('queued','delivered','succeeded','failed','expired')",
            name="ck_node_operation_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(ForeignKey("department_nodes.id"), nullable=False)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_code: Mapped[str | None] = mapped_column(String(64))
    result_summary: Mapped[str] = mapped_column(String(500), default="", nullable=False)


class SkillDefinition(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "skill_definitions"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_skill_tenant_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stable_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class SkillDraft(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "skill_drafts"
    __table_args__ = (UniqueConstraint("skill_id", name="uq_skill_active_draft"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skill_definitions.id"), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    test_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    tested_digest: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str] = mapped_column(String(36), nullable=False)


class SkillVersion(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skill_definitions.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    published_by: Mapped[str] = mapped_column(String(36), nullable=False)


class CapabilityVersion(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "capability_versions"
    __table_args__ = (UniqueConstraint("capability_id", "version", name="uq_capability_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    capability_id: Mapped[str] = mapped_column(ForeignKey("capability_definitions.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    composition_json: Mapped[str] = mapped_column(Text, nullable=False)
    published_by: Mapped[str] = mapped_column(String(36), nullable=False)


class Connector(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "connectors"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_connector_tenant_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)


class ConnectorInstance(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "connector_instances"
    __table_args__ = (Index("ix_connector_instance_org_status", "tenant_id", "organization_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)


class ConnectorTool(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "connector_tools"
    __table_args__ = (
        UniqueConstraint("connector_instance_id", "external_name", name="uq_connector_external_tool"),
        UniqueConstraint("tenant_id", "internal_tool_key", name="uq_connector_internal_tool"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    connector_instance_id: Mapped[str] = mapped_column(ForeignKey("connector_instances.id"), nullable=False)
    external_name: Mapped[str] = mapped_column(String(128), nullable=False)
    internal_tool_key: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    input_schema_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    provider_version: Mapped[str] = mapped_column(String(64), default="1", nullable=False)
    risk_level: Mapped[str | None] = mapped_column(String(8))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Credential(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "credentials"
    __table_args__ = (Index("ix_credentials_scope", "tenant_id", "scope_type", "owner_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
