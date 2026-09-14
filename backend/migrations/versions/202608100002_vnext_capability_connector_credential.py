"""add VNext Skill, Connector, and Credential governance records

Revision ID: 202608100002
Revises: 202608100001
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202608100002"
down_revision: str | None = "202608100001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    """执行当前组件的内部受限处理逻辑。"""
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.create_table(
        "skill_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("stable_version", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "key", name="uq_skill_tenant_key"),
    )
    op.create_table(
        "skill_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("skill_id", sa.String(36), sa.ForeignKey("skill_definitions.id"), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("test_status", sa.String(32), nullable=False),
        sa.Column("tested_digest", sa.String(64)),
        sa.Column("updated_by", sa.String(36), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("skill_id", name="uq_skill_active_draft"),
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("skill_id", sa.String(36), sa.ForeignKey("skill_definitions.id"), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("published_by", sa.String(36), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_version"),
    )
    op.create_table(
        "capability_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("capability_id", sa.String(36), sa.ForeignKey("capability_definitions.id"), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("composition_json", sa.Text(), nullable=False),
        sa.Column("published_by", sa.String(36), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("capability_id", "version", name="uq_capability_version"),
    )
    op.create_table(
        "connectors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "key", name="uq_connector_tenant_key"),
    )
    op.create_table(
        "connector_instances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("auth_mode", sa.String(32), nullable=False),
        sa.Column("credential_ref", sa.String(36)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_error", sa.Text()),
        *_timestamps(),
    )
    op.create_index("ix_connector_instance_org_status", "connector_instances", ["tenant_id", "organization_id", "status"])
    op.create_table(
        "connector_tools",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("connector_instance_id", sa.String(36), sa.ForeignKey("connector_instances.id"), nullable=False),
        sa.Column("external_name", sa.String(128), nullable=False),
        sa.Column("internal_tool_key", sa.String(128)),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema_json", sa.Text(), nullable=False),
        sa.Column("provider_version", sa.String(64), nullable=False),
        sa.Column("risk_level", sa.String(8)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("connector_instance_id", "external_name", name="uq_connector_external_tool"),
        sa.UniqueConstraint("tenant_id", "internal_tool_key", name="uq_connector_internal_tool"),
    )
    op.create_table(
        "credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("connector_id", sa.String(36), sa.ForeignKey("connectors.id"), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_credentials_scope", "credentials", ["tenant_id", "scope_type", "owner_id", "status"])


def downgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.drop_index("ix_credentials_scope", table_name="credentials")
    op.drop_table("credentials")
    op.drop_table("connector_tools")
    op.drop_index("ix_connector_instance_org_status", table_name="connector_instances")
    op.drop_table("connector_instances")
    op.drop_table("connectors")
    op.drop_table("capability_versions")
    op.drop_table("skill_versions")
    op.drop_table("skill_drafts")
    op.drop_table("skill_definitions")
