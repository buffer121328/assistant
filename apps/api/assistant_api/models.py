from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_APPROVAL = "waiting_approval"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)


class PlatformAccount(TimestampMixin, Base):
    __tablename__ = "platform_accounts"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "platform_user_id",
            name="uq_platform_accounts_platform_user_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    platform_user_id: Mapped[str] = mapped_column(String(255), nullable=False)


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_user_created_at", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default=TaskStatus.PENDING.value,
        nullable=False,
    )
    workflow_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessedMessage(TimestampMixin, Base):
    __tablename__ = "processed_messages"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "message_id",
            name="uq_processed_messages_platform_message_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response_target: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)


class Memory(TimestampMixin, Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    memory_type: Mapped[str] = mapped_column(
        String(64),
        default="preference",
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance_score: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ScheduledTaskRun(Base):
    __tablename__ = "scheduled_task_runs"
    __table_args__ = (
        UniqueConstraint(
            "schedule_key",
            "scheduled_for",
            name="uq_scheduled_task_runs_slot",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class ModelLog(Base):
    __tablename__ = "model_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    model_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class ToolLog(Base):
    __tablename__ = "tool_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="succeeded", nullable=False)
    input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class SkillAuditLog(TimestampMixin, Base):
    __tablename__ = "skill_audit_logs"
    __table_args__ = (
        Index("ix_skill_audit_logs_actor_created_at", "actor_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    skill_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_task_status", "task_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    decided_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
