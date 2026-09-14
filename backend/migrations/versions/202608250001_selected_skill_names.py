"""Persist user-selected Skill names for governed task snapshots."""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "202608250001"
down_revision: str | None = "202608230002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("requested_skill_names_json", sa.Text(), server_default="[]", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("tasks", "requested_skill_names_json")
