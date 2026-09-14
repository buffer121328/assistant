"""add governed Artifact lifecycle persistence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608110003"
down_revision: str | None = "202608110002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.add_column(
        "governance_audits",
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_governance_audit_conversation_resource",
        "governance_audits",
        ["conversation_id", "resource_id"],
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=True),
        sa.Column("owner_type", sa.String(length=32), server_default="user", nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("display_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_reference", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), server_default="1", nullable=False),
        sa.Column("generation_method", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("source_reference_versions_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("visibility", sa.String(length=32), server_default="private", nullable=False),
        sa.Column("sensitivity", sa.String(length=32), server_default="normal", nullable=False),
        sa.Column("lifecycle_state", sa.String(length=32), server_default="registered", nullable=False),
        sa.Column("publication_state", sa.String(length=32), server_default="unpublished", nullable=False),
        sa.Column("retention_state", sa.String(length=32), server_default="retained", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "idempotency_key", name="uq_artifacts_task_idempotency"),
    )
    op.create_index(
        "ix_artifacts_owner_conversation_created",
        "artifacts",
        ["owner_id", "conversation_id", "created_at"],
    )
    op.create_index(
        "ix_artifacts_tenant_organization_state",
        "artifacts",
        ["tenant_id", "organization_id", "lifecycle_state"],
    )
    op.create_index("ix_artifacts_task_run", "artifacts", ["task_id", "run_id"])
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('public.artifacts') IS NOT NULL THEN
            ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;
            ALTER TABLE artifacts FORCE ROW LEVEL SECURITY;
            CREATE POLICY app_tenant_isolation ON artifacts
              USING (tenant_id = app_current_tenant())
              WITH CHECK (tenant_id = app_current_tenant());
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('public.artifacts') IS NOT NULL THEN
            DROP POLICY IF EXISTS app_tenant_isolation ON artifacts;
            ALTER TABLE artifacts NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE artifacts DISABLE ROW LEVEL SECURITY;
          END IF;
        END $$;
        """
    )
    op.drop_index("ix_artifacts_task_run", table_name="artifacts")
    op.drop_index("ix_artifacts_tenant_organization_state", table_name="artifacts")
    op.drop_index("ix_artifacts_owner_conversation_created", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index(
        "ix_governance_audit_conversation_resource",
        table_name="governance_audits",
    )
    op.drop_column("governance_audits", "conversation_id")
