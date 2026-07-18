"""add v2 monitoring and memory evolution fields

Revision ID: 202607130001
Revises: 202607090001
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202607130001"
down_revision: str | None = "202607090001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "importance_score",
            sa.Integer(),
            server_default="5",
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "access_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memories",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memories",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "scheduled_task_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_key", sa.String(length=128), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schedule_key",
            "scheduled_for",
            name="uq_scheduled_task_runs_slot",
        ),
    )


def downgrade() -> None:
    op.drop_table("scheduled_task_runs")
    op.drop_column("memories", "archived_at")
    op.drop_column("memories", "expires_at")
    op.drop_column("memories", "last_accessed_at")
    op.drop_column("memories", "access_count")
    op.drop_column("memories", "importance_score")
