from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_id, utc_now


class User(TimestampMixin, Base):
    """表示 处理 user 的后端数据结构或服务对象。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)


class PlatformAccount(TimestampMixin, Base):
    """表示 处理 platform account 的后端数据结构或服务对象。"""

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
    """表示 处理 account connection 的后端数据结构或服务对象。"""

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
    """表示 处理 connection audit log 的后端数据结构或服务对象。"""

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
