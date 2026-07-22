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
from .enums import ApprovalType, TaskStatus


class Task(TimestampMixin, Base):
    """表示 处理 task 的后端数据结构或服务对象。"""

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
    """表示 处理 agent run 的后端数据结构或服务对象。"""

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
    """表示 处理 task event 的后端数据结构或服务对象。"""

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


class Approval(TimestampMixin, Base):
    """表示 处理 approval 的后端数据结构或服务对象。"""

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
