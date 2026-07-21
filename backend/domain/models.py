from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Float,
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


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "channel",
            "external_key",
            name="uq_conversations_user_channel_external",
        ),
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_key: Mapped[str | None] = mapped_column(String(512))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationSummary(TimestampMixin, Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "status", name="uq_conversation_summary_status"
        ),
        Index(
            "ix_conversation_summaries_user_conversation", "user_id", "conversation_id"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_start_message_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_messages.id"), nullable=False
    )
    source_end_message_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_messages.id"), nullable=False
    )
    source_message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class MemoryBlock(TimestampMixin, Base):
    __tablename__ = "memory_blocks"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "block_type",
            "scope_kind",
            "scope_id",
            name="uq_memory_blocks_scope",
        ),
        Index("ix_memory_blocks_user_scope", "user_id", "scope_kind", "scope_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    block_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    character_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    token_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    read_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    update_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
    __table_args__ = (
        Index("ix_account_connections_user_provider", "user_id", "provider"),
    )

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
    __table_args__ = (
        Index("ix_connection_audit_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("account_connections.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


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
        UniqueConstraint(
            "document_id", "ordinal", name="uq_knowledge_chunks_document_ordinal"
        ),
        Index("ix_knowledge_chunks_user_document", "user_id", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class ImportAudit(Base):
    __tablename__ = "import_audits"
    __table_args__ = (Index("ix_import_audits_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_documents.id")
    )
    source_label: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Reminder(TimestampMixin, Base):
    __tablename__ = "reminders"
    __table_args__ = (
        Index("ix_reminders_user_due_status", "user_id", "due_at", "status"),
    )

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
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        Index("ix_delivery_attempts_outbox_created", "outbox_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    outbox_id: Mapped[str] = mapped_column(
        ForeignKey("notification_outbox.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


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
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True
    )
    model_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", name="uq_agent_runs_task_attempt"),
        Index("ix_agent_runs_task_started", "task_id", "started_at"),
        Index("ix_agent_runs_user_started", "user_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_profile: Mapped[str | None] = mapped_column(String(128), nullable=True)
    graph_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tool_snapshot_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_task_events_task_sequence"),
        Index("ix_task_events_task_sequence", "task_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        Index(
            "ix_conversation_messages_conversation_created",
            "conversation_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ProcessedMessage(TimestampMixin, Base):
    __tablename__ = "processed_messages"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "adapter",
            "message_id",
            name="uq_processed_messages_platform_adapter_message_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sender_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    conversation_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response_target: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    delivery_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class Memory(TimestampMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source_kind",
            "source_message_id",
            name="uq_memories_user_source_message",
        ),
        Index(
            "ix_memories_user_status_scope",
            "user_id",
            "status",
            "scope_kind",
            "scope_id",
        ),
        Index("ix_memories_content_hash", "user_id", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    scope_kind: Mapped[str] = mapped_column(
        String(32), default="user/global", nullable=False
    )
    scope_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    memory_type: Mapped[str] = mapped_column(
        String(64),
        default="preference",
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(72), default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    sensitivity: Mapped[str] = mapped_column(
        String(32), default="public", nullable=False
    )
    confirmed_by_user: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    event_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("memories.id"), nullable=True
    )
    source_kind: Mapped[str] = mapped_column(
        String(64), default="legacy_explicit", nullable=False
    )
    source_trust: Mapped[str] = mapped_column(
        String(32), default="trusted_legacy", nullable=False
    )
    source_spans_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    candidate_links_json: Mapped[str] = mapped_column(
        Text, default="[]", nullable=False
    )
    reason_code: Mapped[str] = mapped_column(
        String(64), default="legacy_explicit", nullable=False
    )
    source_conversation_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    source_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id"), nullable=True
    )
    extractor_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[str] = mapped_column(
        String(64), default="v6-01", nullable=False
    )
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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


class MemoryLink(Base):
    __tablename__ = "memory_links"
    __table_args__ = (
        UniqueConstraint(
            "source_memory_id",
            "target_memory_id",
            "link_type",
            name="uq_memory_links_edge",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_memory_id: Mapped[str] = mapped_column(
        ForeignKey("memories.id"), nullable=False
    )
    target_memory_id: Mapped[str] = mapped_column(
        ForeignKey("memories.id"), nullable=False
    )
    link_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    source_evidence_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MemoryFeedback(Base):
    __tablename__ = "memory_feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    feedback_type: Mapped[str] = mapped_column(String(32), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    retrieval_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MemoryIndexOutbox(TimestampMixin, Base):
    __tablename__ = "memory_index_outbox"
    __table_args__ = (
        UniqueConstraint(
            "memory_id", "operation", "status", name="uq_memory_index_outbox_pending"
        ),
        Index("ix_memory_index_outbox_status_updated", "status", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MemoryRetrievalTrace(Base):
    __tablename__ = "memory_retrieval_traces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    time_intent: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    injected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    injected_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MemoryRetrievalTraceItem(Base):
    __tablename__ = "memory_retrieval_trace_items"
    __table_args__ = (
        UniqueConstraint(
            "trace_id", "memory_id", name="uq_memory_retrieval_trace_item"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trace_id: Mapped[str] = mapped_column(
        ForeignKey("memory_retrieval_traces.id"), nullable=False
    )
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), nullable=False)
    filter_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    component_scores_json: Mapped[str] = mapped_column(Text, nullable=False)
    final_score: Mapped[float] = mapped_column(Float, nullable=False)
    final_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    injected_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class MemoryConsolidationDigest(Base):
    __tablename__ = "memory_consolidation_digests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    digest_type: Mapped[str] = mapped_column(String(16), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MemoryConsolidationRun(Base):
    __tablename__ = "memory_consolidation_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "run_type",
            "window_start",
            "window_end",
            name="uq_memory_consolidation_window",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    merged_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    derived_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reconciliation_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    digest_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_consolidation_digests.id"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MemoryConsolidationDecision(Base):
    __tablename__ = "memory_consolidation_decisions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "source_memory_id",
            "action",
            name="uq_memory_consolidation_decision",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("memory_consolidation_runs.id"), nullable=False
    )
    source_memory_id: Mapped[str] = mapped_column(
        ForeignKey("memories.id"), nullable=False
    )
    target_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("memories.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MemoryPolicy(TimestampMixin, Base):
    __tablename__ = "memory_policies"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "policy_key",
            "scope_kind",
            "scope_id",
            name="uq_memory_policies_user_scope",
        ),
        Index("ix_memory_policies_user_key", "user_id", "policy_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    policy_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AgentSchedule(TimestampMixin, Base):
    __tablename__ = "agent_schedules"
    __table_args__ = (
        Index("ix_agent_schedules_user_next_run", "user_id", "next_run_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    catch_up_policy: Mapped[str] = mapped_column(String(16), default="skip", nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentScheduleRun(Base):
    __tablename__ = "agent_schedule_runs"
    __table_args__ = (
        UniqueConstraint("schedule_id", "scheduled_for", name="uq_agent_schedule_runs_slot"),
        Index("ix_agent_schedule_runs_schedule_created", "schedule_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("agent_schedules.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="materialized", nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ModelLog(Base):
    __tablename__ = "model_logs"
    __table_args__ = (Index("ix_model_logs_agent_run_id", "agent_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"),
        nullable=True,
    )
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
    __table_args__ = (Index("ix_evolution_changes_user_status", "user_id", "status"),)

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


class MemoryReleaseReport(Base):
    __tablename__ = "memory_release_reports"
    __table_args__ = (
        Index("ix_memory_release_reports_user_scope", "user_id", "scope_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    automated_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    manual_evidence_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    gate_reasons_json: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    case_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MemoryRetrievalPolicyVersion(Base):
    __tablename__ = "memory_retrieval_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "scope_key",
            "version",
            name="uq_memory_retrieval_policy_version",
        ),
        Index(
            "ix_memory_retrieval_policy_active",
            "user_id",
            "scope_key",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope_key: Mapped[str] = mapped_column(String(320), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_retrieval_policy_versions.id"), nullable=True
    )
    activated_report_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_release_reports.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MemoryEffectiveness(TimestampMixin, Base):
    __tablename__ = "memory_effectiveness"
    __table_args__ = (
        UniqueConstraint("user_id", "memory_id", name="uq_memory_effectiveness_owner"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), nullable=False)
    helpful_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    harmful_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class MemoryEffectivenessEvent(Base):
    __tablename__ = "memory_effectiveness_events"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "memory_id",
            "evidence_key",
            name="uq_memory_effectiveness_event",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), nullable=False)
    evidence_key: Mapped[str] = mapped_column(String(128), nullable=False)
    feedback_type: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
