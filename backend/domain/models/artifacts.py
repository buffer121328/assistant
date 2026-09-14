from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_id
from domain.policies.enterprise import LOCAL_TENANT_ID


class ArtifactRecord(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "idempotency_key",
            name="uq_artifacts_task_idempotency",
        ),
        Index(
            "ix_artifacts_owner_conversation_created",
            "owner_id",
            "conversation_id",
            "created_at",
        ),
        Index(
            "ix_artifacts_tenant_organization_state",
            "tenant_id",
            "organization_id",
            "lifecycle_state",
        ),
        Index("ix_artifacts_task_run", "task_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        default=LOCAL_TENANT_ID,
        server_default=LOCAL_TENANT_ID,
        nullable=False,
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    owner_type: Mapped[str] = mapped_column(
        String(32), default="user", server_default="user", nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    display_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(
        String(64), default="1", server_default="1", nullable=False
    )
    generation_method: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_reference_versions_json: Mapped[str] = mapped_column(
        Text, default="{}", server_default="{}", nullable=False
    )
    visibility: Mapped[str] = mapped_column(
        String(32), default="private", server_default="private", nullable=False
    )
    sensitivity: Mapped[str] = mapped_column(
        String(32), default="normal", server_default="normal", nullable=False
    )
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), default="registered", server_default="registered", nullable=False
    )
    publication_state: Mapped[str] = mapped_column(
        String(32), default="unpublished", server_default="unpublished", nullable=False
    )
    retention_state: Mapped[str] = mapped_column(
        String(32), default="retained", server_default="retained", nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
