"""add agent schedules

Revision ID: 202607190002
Revises: 202607190001
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202607190002"
down_revision: str | None = "202607190001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "agent_schedules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("catch_up_policy", sa.String(length=16), nullable=False, server_default="skip"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index("ix_agent_schedules_user_next_run", "agent_schedules", ["user_id", "next_run_at"])
    op.create_table(
        "agent_schedule_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("schedule_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="materialized"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["agent_schedules.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.UniqueConstraint("schedule_id", "scheduled_for", name="uq_agent_schedule_runs_slot"),
    )
    op.create_index("ix_agent_schedule_runs_schedule_created", "agent_schedule_runs", ["schedule_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_schedule_runs_schedule_created", table_name="agent_schedule_runs")
    op.drop_table("agent_schedule_runs")
    op.drop_index("ix_agent_schedules_user_next_run", table_name="agent_schedules")
    op.drop_table("agent_schedules")
