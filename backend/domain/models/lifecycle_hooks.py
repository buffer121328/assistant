from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_id, utc_now


class LifecycleHookExecution(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "lifecycle_hook_executions"
    __table_args__ = (
        UniqueConstraint("execution_key", name="uq_lifecycle_hook_execution_key"),
        Index(
            "ix_lifecycle_hook_executions_task_created",
            "task_id",
            "created_at",
        ),
        Index(
            "ix_lifecycle_hook_executions_event_hook",
            "event_id",
            "hook_name",
            "hook_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    execution_key: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    hook_name: Mapped[str] = mapped_column(String(128), nullable=False)
    hook_version: Mapped[str] = mapped_column(String(64), nullable=False)
    hook_source: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_state: Mapped[str] = mapped_column(String(32), default="first_attempt", nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
