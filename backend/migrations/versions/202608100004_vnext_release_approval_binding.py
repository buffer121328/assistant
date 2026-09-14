"""bind governed approvals to exact runs and authority revisions

Revision ID: 202608100004
Revises: 202608100003
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202608100004"
down_revision: str | None = "202608100003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.add_column("approvals", sa.Column("run_id", sa.String(36), nullable=True))
    op.add_column(
        "approvals", sa.Column("authority_revision", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_approvals_run",
        "approvals",
        "agent_runs",
        ["run_id"],
        ["id"],
    )
    op.create_index(
        "ix_approvals_run_status",
        "approvals",
        ["run_id", "status"],
    )


def downgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.drop_index("ix_approvals_run_status", table_name="approvals")
    op.drop_constraint("fk_approvals_run", "approvals", type_="foreignkey")
    op.drop_column("approvals", "authority_revision")
    op.drop_column("approvals", "run_id")
