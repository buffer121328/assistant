"""add Skill lifecycle audit logs

Revision ID: 202607130003
Revises: 202607130002
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202607130003"
down_revision: str | None = "202607130002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行数据库迁移升级步骤。"""
    op.create_table(
        "skill_audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("skill_name", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_skill_audit_logs_actor_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_skill_audit_logs_actor_created_at",
        "skill_audit_logs",
        ["actor_user_id", "created_at"],
    )


def downgrade() -> None:
    """执行数据库迁移回滚步骤。"""
    op.drop_index(
        "ix_skill_audit_logs_actor_created_at",
        table_name="skill_audit_logs",
    )
    op.drop_table("skill_audit_logs")
