from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_id, utc_now


class Conversation(TimestampMixin, Base):
    """表示 处理 conversation 的后端数据结构或服务对象。"""

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
    """表示 处理 conversation summary 的后端数据结构或服务对象。"""

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


class ConversationMessage(Base):
    """表示 处理 conversation message 的后端数据结构或服务对象。"""

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
    """表示 处理 processed message 的后端数据结构或服务对象。"""

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
