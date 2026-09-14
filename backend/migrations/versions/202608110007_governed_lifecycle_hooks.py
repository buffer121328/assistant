"""add persisted governed lifecycle Hook executions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608110007"
down_revision: str | None = "202608110006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.create_table(
        "lifecycle_hook_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("execution_key", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("hook_name", sa.String(length=128), nullable=False),
        sa.Column("hook_version", sa.String(length=64), nullable=False),
        sa.Column("hook_source", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("failure_class", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retry_state", sa.String(length=32), server_default="first_attempt", nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_key", name="uq_lifecycle_hook_execution_key"),
    )
    op.create_index(
        "ix_lifecycle_hook_executions_task_created",
        "lifecycle_hook_executions",
        ["task_id", "created_at"],
    )
    op.create_index(
        "ix_lifecycle_hook_executions_event_hook",
        "lifecycle_hook_executions",
        ["event_id", "hook_name", "hook_version"],
    )


def downgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.drop_index(
        "ix_lifecycle_hook_executions_event_hook",
        table_name="lifecycle_hook_executions",
    )
    op.drop_index(
        "ix_lifecycle_hook_executions_task_created",
        table_name="lifecycle_hook_executions",
    )
    op.drop_table("lifecycle_hook_executions")
