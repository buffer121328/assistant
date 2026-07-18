"""add typed human approvals

Revision ID: 202607140001
Revises: 202607130003
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202607140001"
down_revision: str | None = "202607130003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column(
            "approval_type",
            sa.String(length=32),
            server_default="tool",
            nullable=False,
        ),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "subject",
            sa.String(length=128),
            server_default="legacy.unknown",
            nullable=False,
        ),
    )
    op.add_column(
        "approvals",
        sa.Column("request_summary", sa.Text(), nullable=True),
    )
    op.execute("UPDATE approvals SET subject = tool_name")
    op.create_index(
        "ix_approvals_task_type_subject_status",
        "approvals",
        ["task_id", "approval_type", "subject", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approvals_task_type_subject_status",
        table_name="approvals",
    )
    op.drop_column("approvals", "request_summary")
    op.drop_column("approvals", "subject")
    op.drop_column("approvals", "approval_type")
