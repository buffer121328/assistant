"""persist the selected office command in Task context snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608110006"
down_revision: str | None = "202608110005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.add_column(
        "task_context_snapshots",
        sa.Column("command_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.drop_column("task_context_snapshots", "command_id")
