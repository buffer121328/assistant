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


class ApprovalType(str, Enum):
    TOOL = "tool"
    PLAN = "plan"
    REVIEW = "review"
    CHANGE = "change"


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


class AccountConnection(TimestampMixin, Base):
    __tablename__ = "account_connections"
    __table_args__ = (Index("ix_account_connections_user_provider", "user_id", "provider"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    credential_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class ConnectionAuditLog(Base):
    __tablename__ = "connection_audit_logs"
    __table_args__ = (Index("ix_connection_audit_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(ForeignKey("account_connections.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class KnowledgeDocument(TimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_path", name="uq_knowledge_documents_user_source"
        ),
        UniqueConstraint(
            "user_id",
            "checksum",
            "parser_version",
            name="uq_knowledge_documents_user_checksum_parser",
        ),
        Index("ix_knowledge_documents_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_label: Mapped[str] = mapped_column(String(255), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_knowledge_chunks_document_ordinal"),
        Index("ix_knowledge_chunks_user_document", "user_id", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class ImportAudit(Base):
    __tablename__ = "import_audits"
    __table_args__ = (Index("ix_import_audits_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_documents.id"))
    source_label: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Reminder(TimestampMixin, Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_user_due_status", "user_id", "due_at", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationOutbox(TimestampMixin, Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_notification_outbox_idempotency"),
        Index("ix_notification_outbox_status_available", "status", "available_at"),
        Index("ix_notification_outbox_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    reminder_id: Mapped[str] = mapped_column(ForeignKey("reminders.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (Index("ix_delivery_attempts_outbox_created", "outbox_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    outbox_id: Mapped[str] = mapped_column(ForeignKey("notification_outbox.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


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


class EvolutionChange(TimestampMixin, Base):
    __tablename__ = "evolution_changes"
    __table_args__ = (
        Index("ix_evolution_changes_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    validation_result: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class EvolutionVersion(Base):
    __tablename__ = "evolution_versions"
    __table_args__ = (
        Index("ix_evolution_versions_change_created", "change_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    change_id: Mapped[str] = mapped_column(
        ForeignKey("evolution_changes.id"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    previous_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    new_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_content: Mapped[str] = mapped_column(Text, nullable=False)
    new_content: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_task_status", "task_id", "status"),
        Index(
            "ix_approvals_task_type_subject_status",
            "task_id",
            "approval_type",
            "subject",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    approval_type: Mapped[str] = mapped_column(
        String(32),
        default=ApprovalType.TOOL.value,
        server_default=ApprovalType.TOOL.value,
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(
        String(128),
        default="legacy.unknown",
        server_default="legacy.unknown",
        nullable=False,
    )
    request_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
