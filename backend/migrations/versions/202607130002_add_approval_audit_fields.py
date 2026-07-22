"""add approval audit fields

Revision ID: 202607130002
Revises: 202607130001
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202607130002"
down_revision: str | None = "202607130001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行数据库迁移升级步骤。"""
    op.add_column(
        "approvals",
        sa.Column(
            "tool_name",
            sa.String(length=128),
            server_default="legacy.unknown",
            nullable=False,
        ),
    )
    op.alter_column("approvals", "tool_name", server_default=None)
    op.add_column(
        "approvals",
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "approvals",
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_approvals_decided_by_user_id_users",
        "approvals",
        "users",
        ["decided_by_user_id"],
        ["id"],
    )
    op.create_index(
        "ix_approvals_task_status",
        "approvals",
        ["task_id", "status"],
    )


def downgrade() -> None:
    """执行数据库迁移回滚步骤。"""
    op.drop_index("ix_approvals_task_status", table_name="approvals")
    op.drop_constraint(
        "fk_approvals_decided_by_user_id_users",
        "approvals",
        type_="foreignkey",
    )
    op.drop_column("approvals", "decided_at")
    op.drop_column("approvals", "decided_by_user_id")
    op.drop_column("approvals", "tool_name")
