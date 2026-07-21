"""add v11 data governance boundaries

Revision ID: 202607210001
Revises: 202607190002
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202607210001"
down_revision: str | None = "202607190002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("processed_messages") as batch_op:
        batch_op.drop_constraint(
            "uq_processed_messages_platform_message_id",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_processed_messages_platform_adapter_message_id",
            ["platform", "adapter", "message_id"],
        )

    op.add_column(
        "model_logs",
        sa.Column(
            "agent_run_id",
            sa.String(length=36),
            sa.ForeignKey("agent_runs.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_model_logs_agent_run_id",
        "model_logs",
        ["agent_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_logs_agent_run_id", table_name="model_logs")
    op.drop_column("model_logs", "agent_run_id")

    with op.batch_alter_table("processed_messages") as batch_op:
        batch_op.drop_constraint(
            "uq_processed_messages_platform_adapter_message_id",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_processed_messages_platform_message_id",
            ["platform", "message_id"],
        )
