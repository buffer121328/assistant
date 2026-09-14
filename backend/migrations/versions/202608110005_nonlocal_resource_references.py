"""add non-local Resource Reference metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608110005"
down_revision: str | None = "202608110004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.add_column(
        "conversation_resource_references",
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversation_resource_references_artifact_id",
        "conversation_resource_references",
        "artifacts",
        ["artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_conversation_resources_artifact_id",
        "conversation_resource_references",
        ["artifact_id"],
    )


def downgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.drop_index(
        "ix_conversation_resources_artifact_id",
        table_name="conversation_resource_references",
    )
    op.drop_constraint(
        "fk_conversation_resource_references_artifact_id",
        "conversation_resource_references",
        type_="foreignkey",
    )
    op.drop_column("conversation_resource_references", "artifact_id")
