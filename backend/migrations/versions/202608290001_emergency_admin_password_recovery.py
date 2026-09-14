"""Add bounded one-time recovery state to local credentials."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608290001"
down_revision: str | None = "202608250001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "local_credentials",
        sa.Column("recovery_failed_attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "local_credentials",
        sa.Column("recovery_locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "local_credentials",
        sa.Column("recovery_consumed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("local_credentials", "recovery_consumed_at")
    op.drop_column("local_credentials", "recovery_locked_until")
    op.drop_column("local_credentials", "recovery_failed_attempt_count")
