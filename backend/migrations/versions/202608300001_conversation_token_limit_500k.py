"""把 conversations 的会话 Token 预算上限从 200000 提升到 500000。

Revision ID: 202608300001
Revises: 202608290001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608300001"
down_revision: str | None = "202608290001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行迁移升级：更新列默认值，并把未超额的存量会话预算提升到 500000。"""
    op.alter_column(
        "conversations",
        "token_limit",
        existing_type=sa.Integer(),
        server_default="500000",
        nullable=False,
    )
    op.execute(
        "UPDATE conversations SET token_limit = 500000 WHERE token_limit < 500000"
    )


def downgrade() -> None:
    """执行迁移回滚：恢复列默认值；已提升的会话保持现值，不回收用量。"""
    op.alter_column(
        "conversations",
        "token_limit",
        existing_type=sa.Integer(),
        server_default="200000",
        nullable=False,
    )
