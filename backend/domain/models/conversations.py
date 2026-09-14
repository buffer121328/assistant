from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from domain.policies.enterprise import LOCAL_TENANT_ID


def _conversation_owner_id(context: object) -> str:
    """Mirror ``user_id`` into owner scope for direct ORM construction.

    Args:
        context: 用于执行当前操作的 context 参数。
    """
    parameters = context.get_current_parameters()  # type: ignore[attr-defined]
    return str(parameters["user_id"])


class Space(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "spaces"
    __table_args__ = (
        Index(
            "ix_spaces_tenant_organization_status",
            "tenant_id",
            "organization_id",
            "status",
        ),
        Index("ix_spaces_created_by_updated", "created_by", "updated_at"),
        CheckConstraint("status IN ('active','archived')", name="ck_spaces_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="active", server_default="active", nullable=False
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SpaceMembership(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "space_memberships"
    __table_args__ = (
        UniqueConstraint("space_id", "user_id", name="uq_space_membership_user"),
        Index("ix_space_memberships_user_status", "tenant_id", "user_id", "status"),
        Index("ix_space_memberships_space_status", "space_id", "status"),
        CheckConstraint(
            "role IN ('owner','editor','viewer')", name="ck_space_memberships_role"
        ),
        CheckConstraint(
            "status IN ('active','revoked')", name="ck_space_memberships_status"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    space_id: Mapped[str] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="active", server_default="active", nullable=False
    )
    granted_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
        Index(
            "ix_conversations_tenant_space_updated",
            "tenant_id",
            "space_id",
            "updated_at",
        ),
        Index(
            "ix_conversations_owner_visibility",
            "owner_type",
            "owner_id",
            "visibility",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        default=LOCAL_TENANT_ID,
        server_default=LOCAL_TENANT_ID,
        nullable=False,
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    space_id: Mapped[str | None] = mapped_column(
        ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )
    owner_type: Mapped[str] = mapped_column(
        String(32), default="user", server_default="user", nullable=False
    )
    owner_id: Mapped[str] = mapped_column(
        String(36), default=_conversation_owner_id, nullable=False
    )
    visibility: Mapped[str] = mapped_column(
        String(32), default="private", server_default="private", nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_key: Mapped[str | None] = mapped_column(String(512))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_limit: Mapped[int] = mapped_column(
        Integer, default=500_000, server_default="500000", nullable=False
    )
    used_input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    used_output_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    reserved_input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    reserved_output_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    token_status: Mapped[str] = mapped_column(
        String(32), default="active", server_default="active", nullable=False
    )
    token_blocked_reason: Mapped[str | None] = mapped_column(String(128))


class ConversationResourceReference(TimestampMixin, Base):
    """定义当前组件的职责和边界。"""

    __tablename__ = "conversation_resource_references"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "resource_kind",
            "source_locator_hash",
            name="uq_conversation_resource_locator",
        ),
        Index(
            "ix_conversation_resources_user_conversation_status",
            "user_id",
            "conversation_id",
            "status",
        ),
        Index(
            "ix_conversation_resources_tenant_organization",
            "tenant_id",
            "organization_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    resource_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_locator_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
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
    visibility: Mapped[str] = mapped_column(
        String(32), default="private", server_default="private", nullable=False
    )
    sensitivity: Mapped[str] = mapped_column(
        String(32), default="normal", server_default="normal", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="attached", server_default="attached", nullable=False
    )
    pinned: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    last_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
