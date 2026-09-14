"""add VNext runtime, governed retrieval, and approval correlations

Revision ID: 202608100003
Revises: 202608100002
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202608100003"
down_revision: str | None = "202608100002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
LOCAL_TENANT_ID = "local"


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.add_column("agent_runs", sa.Column("execution_mode", sa.String(32), server_default="durable", nullable=False))
    op.create_table(
        "governance_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36)),
        sa.Column("task_id", sa.String(36)),
        sa.Column("run_id", sa.String(36)),
        sa.Column("agent_id", sa.String(128)),
        sa.Column("capability_key", sa.String(128)),
        sa.Column("tool_key", sa.String(128)),
        sa.Column("provider_key", sa.String(128)),
        sa.Column("resource_type", sa.String(64)),
        sa.Column("resource_id", sa.String(255)),
        sa.Column("risk", sa.String(8)),
        sa.Column("policy_decision", sa.String(32), nullable=False),
        sa.Column("approval_id", sa.String(36)),
        sa.Column("arguments_hash", sa.String(64)),
        sa.Column("result_status", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(128)),
        sa.Column("cost_usd", sa.Float()),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_governance_audit_tenant_created", "governance_audits", ["tenant_id", "created_at"])
    op.create_index("ix_governance_audit_task_run", "governance_audits", ["task_id", "run_id"])
    op.add_column("task_events", sa.Column("tenant_id", sa.String(36), server_default=LOCAL_TENANT_ID, nullable=False))
    op.add_column("task_events", sa.Column("run_id", sa.String(36), nullable=True))
    op.add_column("task_events", sa.Column("normalized_event_type", sa.String(32), nullable=True))
    op.create_foreign_key("fk_task_events_tenant", "task_events", "tenants", ["tenant_id"], ["id"])
    op.create_foreign_key("fk_task_events_run", "task_events", "agent_runs", ["run_id"], ["id"])

    op.add_column("approvals", sa.Column("request_fingerprint", sa.String(64), nullable=True))
    op.add_column("approvals", sa.Column("capability_key", sa.String(128), nullable=True))
    op.add_column("approvals", sa.Column("policy_decision", sa.String(32), nullable=True))
    op.add_column("approvals", sa.Column("allowed_decisions_json", sa.Text(), server_default='["approve_once","reject"]', nullable=False))
    op.add_column("approvals", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))

    for table in ("memories", "knowledge_documents"):
        op.add_column(table, sa.Column("tenant_id", sa.String(36), server_default=LOCAL_TENANT_ID, nullable=False))
        op.add_column(table, sa.Column("organization_id", sa.String(36), nullable=True))
        op.add_column(table, sa.Column("owner_id", sa.String(36), nullable=True))
        op.add_column(table, sa.Column("visibility", sa.String(32), server_default="private", nullable=False))
        op.create_foreign_key(f"fk_{table}_tenant", table, "tenants", ["tenant_id"], ["id"])
        op.create_foreign_key(f"fk_{table}_organization", table, "organizations", ["organization_id"], ["id"])
        op.execute(sa.text(f"UPDATE {table} SET owner_id = user_id WHERE owner_id IS NULL"))
    op.add_column("knowledge_documents", sa.Column("classification", sa.String(32), server_default="internal", nullable=False))
    op.add_column("knowledge_chunks", sa.Column("tenant_id", sa.String(36), server_default=LOCAL_TENANT_ID, nullable=False))
    op.create_foreign_key("fk_knowledge_chunks_tenant", "knowledge_chunks", "tenants", ["tenant_id"], ["id"])


def downgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.drop_constraint("fk_knowledge_chunks_tenant", "knowledge_chunks", type_="foreignkey")
    op.drop_column("knowledge_chunks", "tenant_id")
    op.drop_column("knowledge_documents", "classification")
    for table in ("knowledge_documents", "memories"):
        op.drop_constraint(f"fk_{table}_organization", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_tenant", table, type_="foreignkey")
        op.drop_column(table, "visibility")
        op.drop_column(table, "owner_id")
        op.drop_column(table, "organization_id")
        op.drop_column(table, "tenant_id")
    op.drop_column("approvals", "expires_at")
    op.drop_column("approvals", "allowed_decisions_json")
    op.drop_column("approvals", "policy_decision")
    op.drop_column("approvals", "capability_key")
    op.drop_column("approvals", "request_fingerprint")
    op.drop_constraint("fk_task_events_run", "task_events", type_="foreignkey")
    op.drop_constraint("fk_task_events_tenant", "task_events", type_="foreignkey")
    op.drop_column("task_events", "run_id")
    op.drop_column("task_events", "normalized_event_type")
    op.drop_column("task_events", "tenant_id")
    op.drop_column("agent_runs", "execution_mode")
    op.drop_index("ix_governance_audit_task_run", table_name="governance_audits")
    op.drop_index("ix_governance_audit_tenant_created", table_name="governance_audits")
    op.drop_table("governance_audits")
