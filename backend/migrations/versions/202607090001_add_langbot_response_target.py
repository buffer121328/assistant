"""add langbot response target

Revision ID: 202607090001
Revises: 202606220002
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202607090001"
down_revision: str | None = "202606220002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行数据库迁移升级步骤。"""
    op.add_column(
        "processed_messages",
        sa.Column("response_target", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """执行数据库迁移回滚步骤。"""
    op.drop_column("processed_messages", "response_target")
