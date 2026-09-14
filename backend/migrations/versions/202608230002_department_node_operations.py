"""Add governed department node, diagnostics, and remote operation ledgers.

Revision ID: 202608230002
Revises: 202608230001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608230002"
down_revision: str | None = "202608230001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "node_enrollments",
    "department_nodes",
    "node_config_snapshots",
    "node_config_applications",
    "diagnostic_packages",
    "node_remote_operations",
)


def upgrade() -> None:
    """Create tenant-scoped node management and operations tables."""
    op.create_table(
        "node_enrollments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("secret_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','used','revoked')", name="ck_node_enrollment_status"),
    )
    op.create_index("ix_node_enrollments_tenant_org_status", "node_enrollments", ["tenant_id", "organization_id", "status"])
    op.create_table(
        "department_nodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("credential_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("agent_version", sa.String(64), nullable=False),
        sa.Column("desired_config_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_config_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepts_new_work", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("health_status", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("health_summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_department_node_status"),
        sa.UniqueConstraint("tenant_id", "organization_id", "name", name="uq_department_node_name"),
    )
    op.create_index("ix_department_nodes_tenant_org_status", "department_nodes", ["tenant_id", "organization_id", "status"])
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("node_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key("fk_tasks_node_id", "department_nodes", ["node_id"], ["id"])
    op.create_index("ix_tasks_node_created_at", "tasks", ["node_id", "created_at"])
    op.create_table(
        "node_config_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "organization_id", "revision", name="uq_node_config_revision"),
        sa.UniqueConstraint("tenant_id", "organization_id", "checksum", name="uq_node_config_checksum"),
    )
    op.create_table(
        "node_config_applications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("department_nodes.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('applied','failed')", name="ck_node_config_application_status"),
        sa.UniqueConstraint("node_id", "revision", name="uq_node_config_application_revision"),
    )
    op.create_table(
        "diagnostic_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id")),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("department_nodes.id")),
        sa.Column("requested_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filters_json", sa.Text(), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_diagnostic_packages_tenant_expires", "diagnostic_packages", ["tenant_id", "expires_at"])
    op.create_table(
        "node_remote_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("department_nodes.id"), nullable=False),
        sa.Column("requested_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operation_type", sa.String(32), nullable=False),
        sa.Column("target_task_id", sa.String(36), sa.ForeignKey("tasks.id")),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("result_code", sa.String(64)),
        sa.Column("result_summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("operation_type IN ('refresh_config','resync_config','pause_new_work','resume_new_work','stop_task','restart_agent')", name="ck_node_operation_type"),
        sa.CheckConstraint("status IN ('queued','delivered','succeeded','failed','expired')", name="ck_node_operation_status"),
        sa.UniqueConstraint("tenant_id", "requested_by", "idempotency_key", name="uq_node_operation_idempotency"),
    )
    op.create_index("ix_node_operations_node_status_expires", "node_remote_operations", ["node_id", "status", "expires_at"])
    if op.get_bind().dialect.name == "postgresql":
        for table in _TENANT_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"CREATE POLICY app_tenant_isolation ON {table} USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")


def downgrade() -> None:
    """Drop only the department-node operations tables in dependency order."""
    if op.get_bind().dialect.name == "postgresql":
        for table in reversed(_TENANT_TABLES):
            op.execute(f"DROP POLICY IF EXISTS app_tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_node_operations_node_status_expires", table_name="node_remote_operations")
    op.drop_table("node_remote_operations")
    op.drop_index("ix_diagnostic_packages_tenant_expires", table_name="diagnostic_packages")
    op.drop_table("diagnostic_packages")
    op.drop_table("node_config_applications")
    op.drop_table("node_config_snapshots")
    op.drop_index("ix_tasks_node_created_at", table_name="tasks")
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_constraint("fk_tasks_node_id", type_="foreignkey")
        batch_op.drop_column("node_id")
    op.drop_index("ix_department_nodes_tenant_org_status", table_name="department_nodes")
    op.drop_table("department_nodes")
    op.drop_index("ix_node_enrollments_tenant_org_status", table_name="node_enrollments")
    op.drop_table("node_enrollments")
