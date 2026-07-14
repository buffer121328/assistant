"""add memory status dispatcher fields

Revision ID: 202606220002
Revises: 202606220001
Create Date: 2026-06-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202606220002"
down_revision: str | None = "202606220001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "memory_type",
            sa.String(length=64),
            server_default="preference",
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "processed_messages",
        sa.Column("chat_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("processed_messages", "chat_id")
    op.drop_column("memories", "deleted_at")
    op.drop_column("memories", "is_active")
    op.drop_column("memories", "memory_type")
