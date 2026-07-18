"""add reminder notification outbox

Revision ID: 202607140005
Revises: 202607140004
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "202607140005"
down_revision: str | None = "202607140004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reminders_user_due_status", "reminders", ["user_id", "due_at", "status"])
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("reminder_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["reminder_id"], ["reminders.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_notification_outbox_idempotency"),
    )
    op.create_index("ix_notification_outbox_status_available", "notification_outbox", ["status", "available_at"])
    op.create_index("ix_notification_outbox_user_status", "notification_outbox", ["user_id", "status"])
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("outbox_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["outbox_id"], ["notification_outbox.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delivery_attempts_outbox_created", "delivery_attempts", ["outbox_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_delivery_attempts_outbox_created", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")
    op.drop_index("ix_notification_outbox_user_status", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_status_available", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_index("ix_reminders_user_due_status", table_name="reminders")
    op.drop_table("reminders")
