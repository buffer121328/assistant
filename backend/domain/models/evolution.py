from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_id, utc_now


class SkillAuditLog(TimestampMixin, Base):
    """表示 处理 skill audit log 的后端数据结构或服务对象。"""

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
    """表示 处理 evolution change 的后端数据结构或服务对象。"""

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
    """表示 处理 evolution version 的后端数据结构或服务对象。"""

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
