"""add conversation history

Revision ID: 202607150003
Revises: 202607150002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607150003"
down_revision: str | None = "202607150002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("external_key", sa.String(512)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "channel", "external_key", name="uq_conversations_user_channel_external"),
    )
    op.create_index("ix_conversations_user_updated", "conversations", ["user_id", "updated_at"])
    op.add_column("tasks", sa.Column("conversation_id", sa.String(36)))
    op.create_foreign_key("fk_tasks_conversation_id", "tasks", "conversations", ["conversation_id"], ["id"])
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36)),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_messages_conversation_created", "conversation_messages", ["conversation_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_conversation_created", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_constraint("fk_tasks_conversation_id", "tasks", type_="foreignkey")
    op.drop_column("tasks", "conversation_id")
    op.drop_index("ix_conversations_user_updated", table_name="conversations")
    op.drop_table("conversations")
