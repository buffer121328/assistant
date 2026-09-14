"""为 conversations 添加按会话累计的模型 Token 预算与用量列。

Revision ID: 202608170001
Revises: 202608150001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608170001"
down_revision: str | None = "202608150001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行迁移升级：为 conversations 新增 token_limit（默认 200000）、已用/预留输入输出 Token 计数、token_status（默认 active）及 token_blocked_reason 共 7 列。"""
    op.add_column(
        "conversations",
        sa.Column("token_limit", sa.Integer(), server_default="200000", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("used_input_tokens", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("used_output_tokens", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("reserved_input_tokens", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("reserved_output_tokens", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("token_status", sa.String(length=32), server_default="active", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("token_blocked_reason", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    """执行迁移回滚：按逆序删除 conversations 表本次新增的 7 个 Token 预算/用量列。"""
    op.drop_column("conversations", "token_blocked_reason")
    op.drop_column("conversations", "token_status")
    op.drop_column("conversations", "reserved_output_tokens")
    op.drop_column("conversations", "reserved_input_tokens")
    op.drop_column("conversations", "used_output_tokens")
    op.drop_column("conversations", "used_input_tokens")
    op.drop_column("conversations", "token_limit")
