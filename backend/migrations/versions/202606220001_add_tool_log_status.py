"""add tool log status

Revision ID: 202606220001
Revises: 202606210001
Create Date: 2026-06-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202606220001"
down_revision: str | None = "202606210001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_logs",
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="succeeded",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tool_logs", "status")
