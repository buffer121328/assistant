"""add V6 hybrid memory retrieval traces

Revision ID: 202607160002
Revises: 202607160001
Create Date: 2026-07-16
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "202607160002"
down_revision: str | None = "202607160001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_retrieval_traces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id")),
        sa.Column("conversation_id", sa.String(36)),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("retrieval_mode", sa.String(32), nullable=False),
        sa.Column("time_intent", sa.String(32), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("injected_count", sa.Integer(), nullable=False),
        sa.Column("injected_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "memory_retrieval_trace_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "trace_id",
            sa.String(36),
            sa.ForeignKey("memory_retrieval_traces.id"),
            nullable=False,
        ),
        sa.Column(
            "memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False
        ),
        sa.Column("filter_reason", sa.String(64), nullable=False),
        sa.Column("component_scores_json", sa.Text(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("final_rank", sa.Integer()),
        sa.Column("injected_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint(
            "trace_id", "memory_id", name="uq_memory_retrieval_trace_item"
        ),
    )


def downgrade() -> None:
    op.drop_table("memory_retrieval_trace_items")
    op.drop_table("memory_retrieval_traces")
