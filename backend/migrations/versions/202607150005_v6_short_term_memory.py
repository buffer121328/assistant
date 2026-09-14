"""add V6 conversation summaries and memory blocks

Revision ID: 202607150005
Revises: 202607150004
Create Date: 2026-07-15
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "202607150005"
down_revision: str | None = "202607150004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行数据库迁移升级步骤。"""
    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column(
            "source_start_message_id",
            sa.String(36),
            sa.ForeignKey("conversation_messages.id"),
            nullable=False,
        ),
        sa.Column(
            "source_end_message_id",
            sa.String(36),
            sa.ForeignKey("conversation_messages.id"),
            nullable=False,
        ),
        sa.Column("source_message_count", sa.Integer(), nullable=False),
        sa.Column("summary_version", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "conversation_id", "status", name="uq_conversation_summary_status"
        ),
    )
    op.create_index(
        "ix_conversation_summaries_user_conversation",
        "conversation_summaries",
        ["user_id", "conversation_id"],
    )
    op.create_table(
        "memory_blocks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("block_type", sa.String(64), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(255)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("character_limit", sa.Integer(), nullable=False),
        sa.Column("token_limit", sa.Integer(), nullable=False),
        sa.Column("read_only", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("update_policy", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "block_type",
            "scope_kind",
            "scope_id",
            name="uq_memory_blocks_scope",
        ),
    )
    op.create_index(
        "ix_memory_blocks_user_scope",
        "memory_blocks",
        ["user_id", "scope_kind", "scope_id"],
    )


def downgrade() -> None:
    """执行数据库迁移回滚步骤。"""
    op.drop_index("ix_memory_blocks_user_scope", table_name="memory_blocks")
    op.drop_table("memory_blocks")
    op.drop_index(
        "ix_conversation_summaries_user_conversation",
        table_name="conversation_summaries",
    )
    op.drop_table("conversation_summaries")
