"""expand processed messages bridge ledger

Revision ID: 202607190001
Revises: 202607160005
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202607190001"
down_revision: str | None = "202607160005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("processed_messages", sa.Column("adapter", sa.String(length=64), nullable=True))
    op.add_column("processed_messages", sa.Column("sender_id", sa.String(length=255), nullable=True))
    op.add_column(
        "processed_messages",
        sa.Column("conversation_type", sa.String(length=64), nullable=True),
    )
    op.add_column("processed_messages", sa.Column("message_text", sa.Text(), nullable=True))
    op.add_column(
        "processed_messages",
        sa.Column("intent_outcome", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "processed_messages",
        sa.Column("delivery_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "processed_messages",
        sa.Column(
            "delivery_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "processed_messages",
        sa.Column("delivery_error_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "processed_messages",
        sa.Column("delivery_result_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "processed_messages",
        sa.Column("delivery_last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("processed_messages", "delivery_last_attempt_at")
    op.drop_column("processed_messages", "delivery_result_json")
    op.drop_column("processed_messages", "delivery_error_summary")
    op.drop_column("processed_messages", "delivery_attempt_count")
    op.drop_column("processed_messages", "delivery_status")
    op.drop_column("processed_messages", "intent_outcome")
    op.drop_column("processed_messages", "message_text")
    op.drop_column("processed_messages", "conversation_type")
    op.drop_column("processed_messages", "sender_id")
    op.drop_column("processed_messages", "adapter")
