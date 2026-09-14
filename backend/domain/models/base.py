from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """处理 utc now。"""
    return datetime.now(UTC)


def new_id() -> str:
    """处理 new id。"""
    return str(uuid4())


class Base(DeclarativeBase):
    """表示 处理 base 的后端数据结构或服务对象。"""

    pass


class TimestampMixin:
    """表示 处理 timestamp mixin 的后端数据结构或服务对象。"""

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
