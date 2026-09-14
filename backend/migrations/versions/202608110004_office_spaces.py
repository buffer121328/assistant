"""add tenant-scoped Office Spaces and Conversation authorization scope."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608110004"
down_revision: str | None = "202608110003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.create_table(
        "spaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_spaces_status"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_spaces_tenant_organization_status",
        "spaces",
        ["tenant_id", "organization_id", "status"],
    )
    op.create_index(
        "ix_spaces_created_by_updated", "spaces", ["created_by", "updated_at"]
    )
    op.create_table(
        "space_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("space_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("granted_by", sa.String(length=36), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner','editor','viewer')",
            name="ck_space_memberships_role",
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_space_memberships_status",
        ),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", "user_id", name="uq_space_membership_user"),
    )
    op.create_index(
        "ix_space_memberships_user_status",
        "space_memberships",
        ["tenant_id", "user_id", "status"],
    )
    op.create_index(
        "ix_space_memberships_space_status",
        "space_memberships",
        ["space_id", "status"],
    )

    op.add_column(
        "conversations",
        sa.Column("tenant_id", sa.String(length=36), server_default="local", nullable=True),
    )
    op.add_column(
        "conversations", sa.Column("organization_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("space_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "conversations",
        sa.Column("owner_type", sa.String(length=32), server_default="user", nullable=False),
    )
    op.add_column(
        "conversations", sa.Column("owner_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "conversations",
        sa.Column("visibility", sa.String(length=32), server_default="private", nullable=False),
    )
    op.execute(
        """
        UPDATE conversations AS conversation
        SET tenant_id = users.tenant_id,
            owner_id = conversation.user_id
        FROM users
        WHERE users.id = conversation.user_id
        """
    )
    op.alter_column("conversations", "tenant_id", nullable=False)
    op.alter_column("conversations", "owner_id", nullable=False)
    op.create_foreign_key(
        "fk_conversations_tenant_id", "conversations", "tenants", ["tenant_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_conversations_organization_id",
        "conversations",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_conversations_space_id",
        "conversations",
        "spaces",
        ["space_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_conversations_tenant_space_updated",
        "conversations",
        ["tenant_id", "space_id", "updated_at"],
    )
    op.create_index(
        "ix_conversations_owner_visibility",
        "conversations",
        ["owner_type", "owner_id", "visibility"],
    )
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('public.spaces') IS NOT NULL THEN
            ALTER TABLE spaces ENABLE ROW LEVEL SECURITY;
            ALTER TABLE spaces FORCE ROW LEVEL SECURITY;
            CREATE POLICY app_tenant_isolation ON spaces
              USING (tenant_id = app_current_tenant())
              WITH CHECK (tenant_id = app_current_tenant());
          END IF;
          IF to_regclass('public.space_memberships') IS NOT NULL THEN
            ALTER TABLE space_memberships ENABLE ROW LEVEL SECURITY;
            ALTER TABLE space_memberships FORCE ROW LEVEL SECURITY;
            CREATE POLICY app_tenant_isolation ON space_memberships
              USING (tenant_id = app_current_tenant())
              WITH CHECK (tenant_id = app_current_tenant());
          END IF;
          IF to_regclass('public.conversations') IS NOT NULL THEN
            ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
            ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
            CREATE POLICY app_tenant_isolation ON conversations
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
          IF to_regclass('public.conversations') IS NOT NULL THEN
            DROP POLICY IF EXISTS app_tenant_isolation ON conversations;
            ALTER TABLE conversations NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE conversations DISABLE ROW LEVEL SECURITY;
          END IF;
          IF to_regclass('public.space_memberships') IS NOT NULL THEN
            DROP POLICY IF EXISTS app_tenant_isolation ON space_memberships;
            ALTER TABLE space_memberships NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE space_memberships DISABLE ROW LEVEL SECURITY;
          END IF;
          IF to_regclass('public.spaces') IS NOT NULL THEN
            DROP POLICY IF EXISTS app_tenant_isolation ON spaces;
            ALTER TABLE spaces NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE spaces DISABLE ROW LEVEL SECURITY;
          END IF;
        END $$;
        """
    )
    op.drop_index("ix_conversations_owner_visibility", table_name="conversations")
    op.drop_index("ix_conversations_tenant_space_updated", table_name="conversations")
    op.drop_constraint("fk_conversations_space_id", "conversations", type_="foreignkey")
    op.drop_constraint(
        "fk_conversations_organization_id", "conversations", type_="foreignkey"
    )
    op.drop_constraint("fk_conversations_tenant_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "visibility")
    op.drop_column("conversations", "owner_id")
    op.drop_column("conversations", "owner_type")
    op.drop_column("conversations", "space_id")
    op.drop_column("conversations", "organization_id")
    op.drop_column("conversations", "tenant_id")
    op.drop_index("ix_space_memberships_space_status", table_name="space_memberships")
    op.drop_index("ix_space_memberships_user_status", table_name="space_memberships")
    op.drop_table("space_memberships")
    op.drop_index("ix_spaces_created_by_updated", table_name="spaces")
    op.drop_index("ix_spaces_tenant_organization_status", table_name="spaces")
    op.drop_table("spaces")
