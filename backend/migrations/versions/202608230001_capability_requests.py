"""Add governed employee capability requests.

Revision ID: 202608230001
Revises: 202608170001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608230001"
down_revision: str | None = "202608170001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the tenant-scoped capability request ledger."""
    op.create_table(
        "capability_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("requester_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("related_task_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','communicating','configuring','testing','pending_approval','published','rejected','closed')",
            name="ck_capability_request_status",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["related_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "requester_id", "idempotency_key",
            name="uq_capability_request_idempotency",
        ),
    )
    op.create_index(
        "ix_capability_requests_requester_created",
        "capability_requests", ["requester_id", "created_at"], unique=False,
    )
    op.create_index(
        "ix_capability_requests_tenant_status_created",
        "capability_requests", ["tenant_id", "status", "created_at"], unique=False,
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE capability_requests ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE capability_requests FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY app_tenant_isolation ON capability_requests
              USING (tenant_id = app_current_tenant())
              WITH CHECK (tenant_id = app_current_tenant())
            """
        )


def downgrade() -> None:
    """Drop only the capability request ledger."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS app_tenant_isolation ON capability_requests")
        op.execute("ALTER TABLE capability_requests NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE capability_requests DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_capability_requests_tenant_status_created", table_name="capability_requests")
    op.drop_index("ix_capability_requests_requester_created", table_name="capability_requests")
    op.drop_table("capability_requests")
