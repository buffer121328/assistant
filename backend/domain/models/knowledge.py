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


class KnowledgeDocument(TimestampMixin, Base):
    """表示 处理 knowledge document 的后端数据结构或服务对象。"""

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
    """表示 处理 knowledge chunk 的后端数据结构或服务对象。"""

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
    """表示 处理 import audit 的后端数据结构或服务对象。"""

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
