"""add V6 memory consolidation and temporal fields

Revision ID: 202607160003
Revises: 202607160002
Create Date: 2026-07-16
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "202607160003"
down_revision: str | None = "202607160002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行数据库迁移升级步骤。"""
    op.add_column("memories", sa.Column("event_time", sa.DateTime(timezone=True)))
    op.add_column("memories", sa.Column("observed_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE memories SET observed_at = created_at WHERE observed_at IS NULL")
    op.create_table(
        "memory_consolidation_digests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("digest_type", sa.String(16), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "memory_consolidation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_type", sa.String(16), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("processed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("merged_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("conflict_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("derived_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "reconciliation_json", sa.Text(), server_default="{}", nullable=False
        ),
        sa.Column("duration_ms", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "digest_id", sa.String(36), sa.ForeignKey("memory_consolidation_digests.id")
        ),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "run_type",
            "window_start",
            "window_end",
            name="uq_memory_consolidation_window",
        ),
    )
    op.create_table(
        "memory_consolidation_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("memory_consolidation_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "source_memory_id",
            sa.String(36),
            sa.ForeignKey("memories.id"),
            nullable=False,
        ),
        sa.Column("target_memory_id", sa.String(36), sa.ForeignKey("memories.id")),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "run_id",
            "source_memory_id",
            "action",
            name="uq_memory_consolidation_decision",
        ),
    )


def downgrade() -> None:
    """执行数据库迁移回滚步骤。"""
    op.drop_table("memory_consolidation_decisions")
    op.drop_table("memory_consolidation_runs")
    op.drop_table("memory_consolidation_digests")
    op.drop_column("memories", "observed_at")
    op.drop_column("memories", "event_time")
