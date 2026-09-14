"""add V6 memory candidate evidence and policies

Revision ID: 202607160001
Revises: 202607150005
Create Date: 2026-07-16
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "202607160001"
down_revision: str | None = "202607150005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行数据库迁移升级步骤。"""
    op.add_column(
        "memories",
        sa.Column(
            "source_trust",
            sa.String(32),
            server_default="trusted_legacy",
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column("source_spans_json", sa.Text(), server_default="[]", nullable=False),
    )
    op.add_column(
        "memories",
        sa.Column(
            "candidate_links_json", sa.Text(), server_default="[]", nullable=False
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "reason_code",
            sa.String(64),
            server_default="legacy_explicit",
            nullable=False,
        ),
    )
    op.create_table(
        "memory_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("policy_key", sa.String(64), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(255)),
        sa.Column("value_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
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
            "policy_key",
            "scope_kind",
            "scope_id",
            name="uq_memory_policies_user_scope",
        ),
    )
    op.create_index(
        "ix_memory_policies_user_key", "memory_policies", ["user_id", "policy_key"]
    )


def downgrade() -> None:
    """执行数据库迁移回滚步骤。"""
    op.drop_index("ix_memory_policies_user_key", table_name="memory_policies")
    op.drop_table("memory_policies")
    op.drop_column("memories", "reason_code")
    op.drop_column("memories", "candidate_links_json")
    op.drop_column("memories", "source_spans_json")
    op.drop_column("memories", "source_trust")
