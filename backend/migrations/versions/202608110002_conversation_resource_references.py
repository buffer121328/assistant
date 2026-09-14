"""add owner-scoped Conversation Resource References."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608110002"
down_revision: str | None = "202608110001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.create_table(
        "conversation_resource_references",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("resource_kind", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_locator_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=True),
        sa.Column("owner_type", sa.String(length=32), server_default="user", nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("visibility", sa.String(length=32), server_default="private", nullable=False),
        sa.Column("sensitivity", sa.String(length=32), server_default="normal", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="attached", nullable=False),
        sa.Column("pinned", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("last_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "resource_kind",
            "source_locator_hash",
            name="uq_conversation_resource_locator",
        ),
    )
    op.create_index(
        "ix_conversation_resources_user_conversation_status",
        "conversation_resource_references",
        ["user_id", "conversation_id", "status"],
    )
    op.create_index(
        "ix_conversation_resources_tenant_organization",
        "conversation_resource_references",
        ["tenant_id", "organization_id"],
    )
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('public.conversation_resource_references') IS NOT NULL THEN
            ALTER TABLE conversation_resource_references ENABLE ROW LEVEL SECURITY;
            ALTER TABLE conversation_resource_references FORCE ROW LEVEL SECURITY;
            CREATE POLICY app_tenant_isolation ON conversation_resource_references
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
          IF to_regclass('public.conversation_resource_references') IS NOT NULL THEN
            DROP POLICY IF EXISTS app_tenant_isolation ON conversation_resource_references;
            ALTER TABLE conversation_resource_references NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE conversation_resource_references DISABLE ROW LEVEL SECURITY;
          END IF;
        END $$;
        """
    )
    op.drop_index(
        "ix_conversation_resources_tenant_organization",
        table_name="conversation_resource_references",
    )
    op.drop_index(
        "ix_conversation_resources_user_conversation_status",
        table_name="conversation_resource_references",
    )
    op.drop_table("conversation_resource_references")
