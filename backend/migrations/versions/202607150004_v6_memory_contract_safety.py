"""add V6 memory contract and safety fields

Revision ID: 202607150004
Revises: 202607150003
Create Date: 2026-07-15
"""

from collections.abc import Sequence
from typing import Any
import sqlalchemy as sa
from alembic import op

revision: str = "202607150004"
down_revision: str | None = "202607150003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns: list[sa.Column[Any]] = [
        sa.Column(
            "scope_kind", sa.String(32), server_default="user/global", nullable=False
        ),
        sa.Column("scope_id", sa.String(255)),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("normalized_content", sa.Text(), server_default="", nullable=False),
        sa.Column("content_hash", sa.String(72), server_default="", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1", nullable=False),
        sa.Column(
            "sensitivity", sa.String(32), server_default="public", nullable=False
        ),
        sa.Column(
            "confirmed_by_user", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("supersedes_id", sa.String(36)),
        sa.Column(
            "source_kind",
            sa.String(64),
            server_default="legacy_explicit",
            nullable=False,
        ),
        sa.Column("source_conversation_id", sa.String(36)),
        sa.Column("source_message_id", sa.String(255)),
        sa.Column("source_task_id", sa.String(36)),
        sa.Column("extractor_version", sa.String(64)),
        sa.Column(
            "policy_version", sa.String(64), server_default="v6-01", nullable=False
        ),
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.false(), nullable=False),
    ]
    for column in columns:
        op.add_column("memories", column)
    op.create_foreign_key(
        "fk_memories_supersedes", "memories", "memories", ["supersedes_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_memories_source_task", "memories", "tasks", ["source_task_id"], ["id"]
    )
    op.execute(
        "UPDATE memories SET normalized_content = trim(content), content_hash = 'md5:' || md5(trim(content)), confirmed_by_user = (memory_type = 'preference'), confirmed_at = CASE WHEN memory_type = 'preference' THEN created_at ELSE NULL END, status = CASE WHEN deleted_at IS NOT NULL THEN 'deleted' WHEN archived_at IS NOT NULL THEN 'archived' ELSE 'active' END"
    )
    op.create_unique_constraint(
        "uq_memories_user_source_message",
        "memories",
        ["user_id", "source_kind", "source_message_id"],
    )
    op.create_index(
        "ix_memories_user_status_scope",
        "memories",
        ["user_id", "status", "scope_kind", "scope_id"],
    )
    op.create_index("ix_memories_content_hash", "memories", ["user_id", "content_hash"])
    op.create_table(
        "memory_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "source_memory_id",
            sa.String(36),
            sa.ForeignKey("memories.id"),
            nullable=False,
        ),
        sa.Column(
            "target_memory_id",
            sa.String(36),
            sa.ForeignKey("memories.id"),
            nullable=False,
        ),
        sa.Column("link_type", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1", nullable=False),
        sa.Column("created_by", sa.String(32), nullable=False),
        sa.Column("source_evidence_id", sa.String(36)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "source_memory_id",
            "target_memory_id",
            "link_type",
            name="uq_memory_links_edge",
        ),
    )
    op.create_table(
        "memory_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("feedback_type", sa.String(32), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id")),
        sa.Column("conversation_id", sa.String(36)),
        sa.Column("retrieval_trace_id", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "memory_index_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(64)),
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
            "memory_id", "operation", "status", name="uq_memory_index_outbox_pending"
        ),
    )


def downgrade() -> None:
    op.drop_table("memory_index_outbox")
    op.drop_table("memory_feedback")
    op.drop_table("memory_links")
    op.drop_index("ix_memories_content_hash", table_name="memories")
    op.drop_index("ix_memories_user_status_scope", table_name="memories")
    op.drop_constraint("uq_memories_user_source_message", "memories", type_="unique")
    op.drop_constraint("fk_memories_source_task", "memories", type_="foreignkey")
    op.drop_constraint("fk_memories_supersedes", "memories", type_="foreignkey")
    for name in [
        "is_pinned",
        "policy_version",
        "extractor_version",
        "source_task_id",
        "source_message_id",
        "source_conversation_id",
        "source_kind",
        "supersedes_id",
        "valid_to",
        "valid_from",
        "confirmed_at",
        "confirmed_by_user",
        "sensitivity",
        "confidence",
        "content_hash",
        "normalized_content",
        "status",
        "scope_id",
        "scope_kind",
    ]:
        op.drop_column("memories", name)
