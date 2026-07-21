"""remove legacy scheduled task runs

Revision ID: 202607210002
Revises: 202607210001
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202607210002"
down_revision: str | None = "202607210001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("scheduled_task_runs")


def downgrade() -> None:
    op.create_table(
        "scheduled_task_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("schedule_key", sa.String(length=128), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "schedule_key",
            "scheduled_for",
            name="uq_scheduled_task_runs_slot",
        ),
    )
