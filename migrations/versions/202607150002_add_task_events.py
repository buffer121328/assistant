"""add task event stream

Revision ID: 202607150002
Revises: 202607150001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607150002"
down_revision: str | None = "202607150001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "sequence", name="uq_task_events_task_sequence"),
    )
    op.create_index("ix_task_events_task_sequence", "task_events", ["task_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_task_events_task_sequence", table_name="task_events")
    op.drop_table("task_events")
