"""create processed messages table

Revision ID: 202606210001
Revises: 202606200001
Create Date: 2026-06-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202606210001"
down_revision: str | None = "202606200001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamp_columns() -> list[sa.Column]:
    """处理 timestamp columns。"""
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    """执行数据库迁移升级步骤。"""
    op.create_table(
        "processed_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.UniqueConstraint(
            "platform",
            "message_id",
            name="uq_processed_messages_platform_message_id",
        ),
    )


def downgrade() -> None:
    """执行数据库迁移回滚步骤。"""
    op.drop_table("processed_messages")
