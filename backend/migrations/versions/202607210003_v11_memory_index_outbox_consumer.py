"""index memory outbox consumer status

Revision ID: 202607210003
Revises: 202607210002
Create Date: 2026-07-21
"""

from collections.abc import Sequence
from alembic import op

revision: str = "202607210003"
down_revision: str | None = "202607210002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行数据库迁移升级步骤。"""
    op.create_index(
        "ix_memory_index_outbox_status_updated",
        "memory_index_outbox",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    """执行数据库迁移回滚步骤。"""
    op.drop_index(
        "ix_memory_index_outbox_status_updated",
        table_name="memory_index_outbox",
    )
