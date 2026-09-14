"""add V6 adaptive memory release gates

Revision ID: 202607160004
Revises: 202607160003
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202607160004"
down_revision: str | None = "202607160003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行数据库迁移升级步骤。"""
    op.create_table(
        "memory_release_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("automated_passed", sa.Boolean(), nullable=False),
        sa.Column("manual_evidence_complete", sa.Boolean(), nullable=False),
        sa.Column("gate_reasons_json", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("case_ids_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_memory_release_reports_user_scope",
        "memory_release_reports",
        ["user_id", "scope_key"],
    )
    op.create_table(
        "memory_retrieval_policy_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(255)),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column(
            "parent_version_id",
            sa.String(36),
            sa.ForeignKey("memory_retrieval_policy_versions.id"),
        ),
        sa.Column(
            "activated_report_id",
            sa.String(36),
            sa.ForeignKey("memory_release_reports.id"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "user_id",
            "scope_key",
            "version",
            name="uq_memory_retrieval_policy_version",
        ),
    )
    op.create_index(
        "ix_memory_retrieval_policy_active",
        "memory_retrieval_policy_versions",
        ["user_id", "scope_key", "status"],
    )
    op.create_table(
        "memory_effectiveness",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False
        ),
        sa.Column("helpful_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("harmful_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
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
            "user_id", "memory_id", name="uq_memory_effectiveness_owner"
        ),
    )
    op.create_table(
        "memory_effectiveness_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "memory_id", sa.String(36), sa.ForeignKey("memories.id"), nullable=False
        ),
        sa.Column("evidence_key", sa.String(128), nullable=False),
        sa.Column("feedback_type", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "memory_id",
            "evidence_key",
            name="uq_memory_effectiveness_event",
        ),
    )


def downgrade() -> None:
    """执行数据库迁移回滚步骤。"""
    op.drop_table("memory_effectiveness_events")
    op.drop_table("memory_effectiveness")
    op.drop_index(
        "ix_memory_retrieval_policy_active",
        table_name="memory_retrieval_policy_versions",
    )
    op.drop_table("memory_retrieval_policy_versions")
    op.drop_index(
        "ix_memory_release_reports_user_scope", table_name="memory_release_reports"
    )
    op.drop_table("memory_release_reports")
