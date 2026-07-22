from __future__ import annotations

from datetime import datetime

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
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, new_id, utc_now


class MemoryBlock(TimestampMixin, Base):
    """表示 处理 memory block 的后端数据结构或服务对象。"""

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


class Memory(TimestampMixin, Base):
    """表示 处理 memory 的后端数据结构或服务对象。"""

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
    """表示 处理 memory link 的后端数据结构或服务对象。"""

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
    """表示 处理 memory feedback 的后端数据结构或服务对象。"""

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
    """表示 处理 memory index outbox 的后端数据结构或服务对象。"""

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
    """表示 处理 memory retrieval trace 的后端数据结构或服务对象。"""

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
    """表示 处理 memory retrieval trace item 的后端数据结构或服务对象。"""

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
    """表示 处理 memory consolidation digest 的后端数据结构或服务对象。"""

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
    """表示 处理 memory consolidation run 的后端数据结构或服务对象。"""

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
    """表示 处理 memory consolidation decision 的后端数据结构或服务对象。"""

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
    """表示 处理 memory policy 的后端数据结构或服务对象。"""

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


class MemoryReleaseReport(Base):
    """表示 处理 memory release report 的后端数据结构或服务对象。"""

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
    """表示 处理 memory retrieval policy version 的后端数据结构或服务对象。"""

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
    """表示 处理 memory effectiveness 的后端数据结构或服务对象。"""

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
    """表示 处理 memory effectiveness event 的后端数据结构或服务对象。"""

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
