"""add VNext enterprise identity policy and governed profiles

Revision ID: 202608100001
Revises: 202607210003
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202608100001"
down_revision: str | None = "202607210003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL_TENANT_ID = "local"
LOCAL_ORGANIZATION_ID = "local-root"


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("authority_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        sa.text(
            "INSERT INTO tenants "
            "(id, name, status, authority_revision, created_at, updated_at) "
            "VALUES (:id, :name, 'active', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ).bindparams(id=LOCAL_TENANT_ID, name="Local")
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("parent_id", sa.String(length=36), sa.ForeignKey("organizations.id")),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "parent_id", "name", name="uq_org_parent_name"),
    )
    op.create_index("ix_organizations_tenant_status", "organizations", ["tenant_id", "status"])
    op.execute(
        sa.text(
            "INSERT INTO organizations "
            "(id, tenant_id, parent_id, name, type, status, created_at, updated_at) "
            "VALUES (:id, :tenant, NULL, :name, 'enterprise', 'active', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ).bindparams(id=LOCAL_ORGANIZATION_ID, tenant=LOCAL_TENANT_ID, name="Local")
    )
    _add_scope_columns()
    _create_governance_tables()
    op.execute(
        sa.text(
            "UPDATE users SET tenant_id = :tenant WHERE tenant_id IS NULL"
        ).bindparams(tenant=LOCAL_TENANT_ID)
    )
    op.execute(
        sa.text(
            "UPDATE tasks SET tenant_id = :tenant, organization_id = :organization, "
            "owner_type = 'user', owner_id = user_id, visibility = 'private' "
            "WHERE tenant_id IS NULL OR owner_id IS NULL"
        ).bindparams(tenant=LOCAL_TENANT_ID, organization=LOCAL_ORGANIZATION_ID)
    )
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("tenant_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.create_foreign_key("fk_users_tenant_id", "tenants", ["tenant_id"], ["id"])
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.alter_column("tenant_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column("owner_type", existing_type=sa.String(length=32), nullable=False)
        batch_op.alter_column("owner_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column("visibility", existing_type=sa.String(length=32), nullable=False)
        batch_op.create_foreign_key("fk_tasks_tenant_id", "tenants", ["tenant_id"], ["id"])
        batch_op.create_foreign_key(
            "fk_tasks_organization_id", "organizations", ["organization_id"], ["id"]
        )


def _add_scope_columns() -> None:
    """执行当前组件的内部受限处理逻辑。"""
    op.add_column("users", sa.Column("tenant_id", sa.String(length=36), nullable=True))
    op.add_column("tasks", sa.Column("tenant_id", sa.String(length=36), nullable=True))
    op.add_column("tasks", sa.Column("organization_id", sa.String(length=36), nullable=True))
    op.add_column("tasks", sa.Column("owner_type", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("owner_id", sa.String(length=36), nullable=True))
    op.add_column("tasks", sa.Column("visibility", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("agent_profile_schema_version", sa.String(length=64), nullable=True))
    op.add_column("tasks", sa.Column("agent_profile_snapshot", sa.Text(), nullable=True))


def _create_governance_tables() -> None:
    """执行当前组件的内部受限处理逻辑。"""
    timestamps = (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        *timestamps,
        sa.UniqueConstraint("tenant_id", "organization_id", "user_id", "role", name="uq_org_membership_role"),
        sa.CheckConstraint(
            "role IN ('member','manager','department_admin','enterprise_admin','capability_publisher','connector_admin','auditor')",
            name="ck_org_membership_role",
        ),
    )
    op.create_index("ix_org_memberships_user_status", "organization_memberships", ["tenant_id", "user_id", "status"])
    op.create_table(
        "policy_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=36)),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=False),
        sa.Column("effect", sa.String(length=16), nullable=False),
        sa.Column("constraints_json", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        *[column._copy() for column in timestamps],
        sa.CheckConstraint(
            "effect IN ('ALLOW','DENY','CONFIRM','APPROVAL')",
            name="ck_policy_rule_effect",
        ),
    )
    op.create_index("ix_policy_rules_tenant_action_status", "policy_rules", ["tenant_id", "action", "status"])
    op.create_table(
        "capability_definitions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("bindings_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        *[column._copy() for column in timestamps],
        sa.UniqueConstraint("tenant_id", "key", "version", name="uq_capability_key_version"),
    )
    op.create_index("ix_capability_definitions_tenant_status", "capability_definitions", ["tenant_id", "status"])
    op.create_table(
        "capability_grants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("source_organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("target_organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("capability_id", sa.String(length=36), sa.ForeignKey("capability_definitions.id"), nullable=False),
        sa.Column("version_constraint", sa.String(length=64), nullable=False),
        sa.Column("can_delegate", sa.Boolean(), nullable=False),
        sa.Column("max_delegate_depth", sa.Integer(), nullable=False),
        sa.Column("constraints_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=32), nullable=False),
        *[column._copy() for column in timestamps],
        sa.CheckConstraint("max_delegate_depth >= 0", name="ck_capability_grant_depth"),
    )
    op.create_index("ix_capability_grants_target_status", "capability_grants", ["tenant_id", "target_organization_id", "status"])


def downgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    for table, index in (
        ("capability_grants", "ix_capability_grants_target_status"),
        ("capability_definitions", "ix_capability_definitions_tenant_status"),
        ("policy_rules", "ix_policy_rules_tenant_action_status"),
        ("organization_memberships", "ix_org_memberships_user_status"),
    ):
        op.drop_index(index, table_name=table)
        op.drop_table(table)
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_constraint("fk_tasks_organization_id", type_="foreignkey")
        batch_op.drop_constraint("fk_tasks_tenant_id", type_="foreignkey")
        for column in (
            "agent_profile_snapshot",
            "agent_profile_schema_version",
            "visibility",
            "owner_id",
            "owner_type",
            "organization_id",
            "tenant_id",
        ):
            batch_op.drop_column(column)
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_tenant_id", type_="foreignkey")
        batch_op.drop_column("tenant_id")
    op.drop_index("ix_organizations_tenant_status", table_name="organizations")
    op.drop_table("organizations")
    op.drop_table("tenants")
