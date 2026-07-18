"""add agent run lifecycle records

Revision ID: 202607160005
Revises: 202607160004
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202607160005"
down_revision: str | None = "202607160004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("agent_profile", sa.String(128)),
        sa.Column("graph_version", sa.String(64)),
        sa.Column("checkpoint_id", sa.String(255)),
        sa.Column("tool_snapshot_revision", sa.Integer()),
        sa.Column("model_class", sa.String(64)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.UniqueConstraint("task_id", "attempt_no", name="uq_agent_runs_task_attempt"),
    )
    op.create_index(
        "ix_agent_runs_task_started", "agent_runs", ["task_id", "started_at"]
    )
    op.create_index(
        "ix_agent_runs_user_started", "agent_runs", ["user_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_user_started", table_name="agent_runs")
    op.drop_index("ix_agent_runs_task_started", table_name="agent_runs")
    op.drop_table("agent_runs")
