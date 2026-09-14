"""add owner-scoped task context snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608110001"
down_revision: str | None = "202608100005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.create_table(
        "task_context_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=True),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column(
            "state", sa.String(length=32), server_default="initial", nullable=False
        ),
        sa.Column(
            "resource_reference_ids_json",
            sa.Text(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "resolved_resource_versions_json",
            sa.Text(),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "memory_scope_snapshot_json",
            sa.Text(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "knowledge_scope_snapshot_json",
            sa.Text(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "capability_snapshot_json",
            sa.Text(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("agent_profile_schema_version", sa.String(length=64)),
        sa.Column("agent_profile_snapshot", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_task_context_snapshots_task"),
    )
    op.create_index(
        "ix_task_context_snapshots_user_created",
        "task_context_snapshots",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_task_context_snapshots_tenant_organization",
        "task_context_snapshots",
        ["tenant_id", "organization_id"],
    )
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('public.tasks') IS NOT NULL THEN
            ALTER TABLE tasks NO FORCE ROW LEVEL SECURITY;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        INSERT INTO task_context_snapshots (
            id,
            task_id,
            conversation_id,
            user_id,
            tenant_id,
            organization_id,
            owner_type,
            owner_id,
            visibility,
            state,
            resource_reference_ids_json,
            resolved_resource_versions_json,
            memory_scope_snapshot_json,
            knowledge_scope_snapshot_json,
            capability_snapshot_json,
            agent_profile_schema_version,
            agent_profile_snapshot,
            created_at,
            finalized_at
        )
        SELECT
            id,
            id,
            conversation_id,
            user_id,
            tenant_id,
            organization_id,
            owner_type,
            COALESCE(owner_id, user_id),
            visibility,
            CASE WHEN agent_profile_snapshot IS NULL THEN 'initial' ELSE 'finalized' END,
            '[]',
            '{}',
            '[]',
            '[]',
            '[]',
            agent_profile_schema_version,
            agent_profile_snapshot,
            created_at,
            CASE WHEN agent_profile_snapshot IS NULL THEN NULL ELSE updated_at END
        FROM tasks
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('public.tasks') IS NOT NULL THEN
            ALTER TABLE tasks FORCE ROW LEVEL SECURITY;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('public.task_context_snapshots') IS NOT NULL THEN
            ALTER TABLE task_context_snapshots ENABLE ROW LEVEL SECURITY;
            ALTER TABLE task_context_snapshots FORCE ROW LEVEL SECURITY;
            CREATE POLICY app_tenant_isolation ON task_context_snapshots
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
          IF to_regclass('public.task_context_snapshots') IS NOT NULL THEN
            DROP POLICY IF EXISTS app_tenant_isolation ON task_context_snapshots;
            ALTER TABLE task_context_snapshots NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE task_context_snapshots DISABLE ROW LEVEL SECURITY;
          END IF;
        END $$;
        """
    )
    op.drop_index(
        "ix_task_context_snapshots_tenant_organization",
        table_name="task_context_snapshots",
    )
    op.drop_index(
        "ix_task_context_snapshots_user_created",
        table_name="task_context_snapshots",
    )
    op.drop_table("task_context_snapshots")
