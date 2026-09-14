"""add governed evolution changes and versions

Revision ID: 202607140002
Revises: 202607140001
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202607140002"
down_revision: str | None = "202607140001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行数据库迁移升级步骤。"""
    op.create_table(
        "evolution_changes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("target_name", sa.String(length=255), nullable=False),
        sa.Column("base_checksum", sa.String(length=64), nullable=False),
        sa.Column("candidate_checksum", sa.String(length=64), nullable=False),
        sa.Column("candidate_content", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("validation_result", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evolution_changes_user_status",
        "evolution_changes",
        ["user_id", "status"],
    )
    op.create_table(
        "evolution_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("change_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("target_name", sa.String(length=255), nullable=False),
        sa.Column("previous_checksum", sa.String(length=64), nullable=False),
        sa.Column("new_checksum", sa.String(length=64), nullable=False),
        sa.Column("previous_content", sa.Text(), nullable=False),
        sa.Column("new_content", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["change_id"], ["evolution_changes.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evolution_versions_change_created",
        "evolution_versions",
        ["change_id", "created_at"],
    )


def downgrade() -> None:
    """执行数据库迁移回滚步骤。"""
    op.drop_index(
        "ix_evolution_versions_change_created",
        table_name="evolution_versions",
    )
    op.drop_table("evolution_versions")
    op.drop_index(
        "ix_evolution_changes_user_status",
        table_name="evolution_changes",
    )
    op.drop_table("evolution_changes")
