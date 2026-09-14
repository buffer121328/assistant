"""add local credential and opaque session records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608150001"
down_revision: str | None = "202608110007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.create_table(
        "local_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("login_name", sa.String(length=128), nullable=False),
        sa.Column("password_verifier", sa.Text(), nullable=False),
        sa.Column("password_version", sa.String(length=32), server_default="scrypt.v1", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("failed_attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "login_name", name="uq_local_credentials_tenant_login"),
        sa.UniqueConstraint("user_id", name="uq_local_credentials_user"),
    )
    op.create_index(
        "ix_local_credentials_tenant_status",
        "local_credentials",
        ["tenant_id", "status"],
    )
    op.create_table(
        "local_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_local_sessions_token_digest"),
    )
    op.create_index(
        "ix_local_sessions_user_status",
        "local_sessions",
        ["user_id", "revoked_at", "expires_at"],
    )


def downgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.drop_index("ix_local_sessions_user_status", table_name="local_sessions")
    op.drop_table("local_sessions")
    op.drop_index("ix_local_credentials_tenant_status", table_name="local_credentials")
    op.drop_table("local_credentials")
