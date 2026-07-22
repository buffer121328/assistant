from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from domain.models import Memory, MemoryFeedback, MemoryIndexOutbox, MemoryLink, utc_now


@dataclass(frozen=True)
class MemoryCreate:
    """表示 处理 memory create 的后端数据结构或服务对象。"""

    user_id: str
    content: str
    normalized_content: str
    content_hash: str
    memory_type: str = "preference"
    status: str = "active"
    scope_kind: str = "user/global"
    scope_id: str | None = None
    sensitivity: str = "public"
    confirmed_by_user: bool = False
    confirmed_at: datetime | None = None
    source_kind: str = "explicit_service"
    source_trust: str = "trusted_user"
    source_spans_json: str = "[]"
    candidate_links_json: str = "[]"
    reason_code: str = "explicit_user_request"
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    source_task_id: str | None = None
    supersedes_id: str | None = None
    importance_score: int = 5
    expires_at: datetime | None = None


class MemoryRepository:
    """表示 处理 memory repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def create_memory(self, data: MemoryCreate) -> Memory:
        """创建 memory。

        Args:
            data: data 参数。
        """
        memory = Memory(
            user_id=data.user_id,
            content=data.content,
            normalized_content=data.normalized_content,
            content_hash=data.content_hash,
            memory_type=data.memory_type,
            status=data.status,
            scope_kind=data.scope_kind,
            scope_id=data.scope_id,
            sensitivity=data.sensitivity,
            confirmed_by_user=data.confirmed_by_user,
            confirmed_at=data.confirmed_at,
            source_kind=data.source_kind,
            source_trust=data.source_trust,
            source_spans_json=data.source_spans_json,
            candidate_links_json=data.candidate_links_json,
            reason_code=data.reason_code,
            source_conversation_id=data.source_conversation_id,
            source_message_id=data.source_message_id,
            source_task_id=data.source_task_id,
            supersedes_id=data.supersedes_id,
            importance_score=data.importance_score,
            expires_at=data.expires_at,
            is_active=True,
        )
        self.session.add(memory)
        await self.session.flush()
        return memory

    async def get_by_source_message(
        self, *, user_id: str, source_kind: str, source_message_id: str
    ) -> Memory | None:
        """获取 by source message。

        Args:
            user_id: user_id 参数。
            source_kind: source_kind 参数。
            source_message_id: source_message_id 参数。
        """
        return await self.session.scalar(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.source_kind == source_kind,
                Memory.source_message_id == source_message_id,
            )
        )

    async def queue_index_operation(
        self, *, memory: Memory, operation: str, error_code: str
    ) -> MemoryIndexOutbox:
        """处理 queue index operation。

        Args:
            memory: memory 参数。
            operation: operation 参数。
            error_code: error_code 参数。
        """
        existing = await self.session.scalar(
            select(MemoryIndexOutbox).where(
                MemoryIndexOutbox.memory_id == memory.id,
                MemoryIndexOutbox.operation == operation,
                MemoryIndexOutbox.status == "pending",
            )
        )
        if existing is not None:
            return existing
        item = MemoryIndexOutbox(
            memory_id=memory.id,
            user_id=memory.user_id,
            operation=operation,
            status="pending",
            last_error_code=error_code,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_memories(
        self,
        *,
        user_id: str,
        status: str | None = None,
        scope_kind: str | None = None,
    ) -> list[Memory]:
        """列出 memories。

        Args:
            user_id: user_id 参数。
            status: status 参数。
            scope_kind: scope_kind 参数。
        """
        statement = select(Memory).where(Memory.user_id == user_id)
        if status is not None:
            statement = statement.where(Memory.status == status)
        if scope_kind is not None:
            statement = statement.where(Memory.scope_kind == scope_kind)
        result = await self.session.scalars(
            statement.order_by(Memory.created_at.asc(), Memory.id.asc())
        )
        return list(result)

    async def list_links_for_memory(self, *, memory_id: str) -> list[MemoryLink]:
        """列出 links for memory。

        Args:
            memory_id: memory_id 参数。
        """
        result = await self.session.scalars(
            select(MemoryLink)
            .where(
                or_(
                    MemoryLink.source_memory_id == memory_id,
                    MemoryLink.target_memory_id == memory_id,
                )
            )
            .order_by(MemoryLink.created_at.asc(), MemoryLink.id.asc())
        )
        return list(result)

    async def list_feedback_for_memory(
        self, *, memory_id: str, user_id: str
    ) -> list[MemoryFeedback]:
        """列出 feedback for memory。

        Args:
            memory_id: memory_id 参数。
            user_id: user_id 参数。
        """
        result = await self.session.scalars(
            select(MemoryFeedback)
            .where(
                MemoryFeedback.memory_id == memory_id,
                MemoryFeedback.user_id == user_id,
            )
            .order_by(MemoryFeedback.created_at.asc(), MemoryFeedback.id.asc())
        )
        return list(result)

    async def list_index_outbox(
        self, *, user_id: str, status: str | None = None
    ) -> list[MemoryIndexOutbox]:
        """列出 index outbox。

        Args:
            user_id: user_id 参数。
            status: status 参数。
        """
        statement = select(MemoryIndexOutbox).where(
            MemoryIndexOutbox.user_id == user_id
        )
        if status is not None:
            statement = statement.where(MemoryIndexOutbox.status == status)
        result = await self.session.scalars(
            statement.order_by(
                MemoryIndexOutbox.created_at.asc(), MemoryIndexOutbox.id.asc()
            )
        )
        return list(result)

    async def list_active_memories(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> list[Memory]:
        """列出 active memories。

        Args:
            user_id: user_id 参数。
            now: now 参数。
        """
        now = now or utc_now()
        result = await self.session.scalars(
            select(Memory)
            .where(Memory.user_id == user_id, *eligible_memory_conditions(now))
            .order_by(Memory.created_at.asc(), Memory.id.asc())
        )
        return list(result)

    async def get_memory_by_user(
        self, *, memory_id: str, user_id: str
    ) -> Memory | None:
        """获取 memory by user。

        Args:
            memory_id: memory_id 参数。
            user_id: user_id 参数。
        """
        return await self.session.scalar(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        )

    async def create_link(
        self,
        *,
        source_memory_id: str,
        target_memory_id: str,
        link_type: str,
        created_by: str,
        confidence: float = 1.0,
        source_evidence_id: str | None = None,
    ) -> MemoryLink:
        """创建 link。

        Args:
            source_memory_id: source_memory_id 参数。
            target_memory_id: target_memory_id 参数。
            link_type: link_type 参数。
            created_by: created_by 参数。
            confidence: confidence 参数。
            source_evidence_id: source_evidence_id 参数。
        """
        existing = await self.session.scalar(
            select(MemoryLink).where(
                MemoryLink.source_memory_id == source_memory_id,
                MemoryLink.target_memory_id == target_memory_id,
                MemoryLink.link_type == link_type,
            )
        )
        if existing is not None:
            return existing
        link = MemoryLink(
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            link_type=link_type,
            created_by=created_by,
            confidence=confidence,
            source_evidence_id=source_evidence_id,
        )
        self.session.add(link)
        await self.session.flush()
        return link

    async def create_feedback(
        self,
        *,
        memory_id: str,
        user_id: str,
        feedback_type: str,
        task_id: str | None = None,
        conversation_id: str | None = None,
        retrieval_trace_id: str | None = None,
    ) -> MemoryFeedback:
        """创建 feedback。

        Args:
            memory_id: memory_id 参数。
            user_id: user_id 参数。
            feedback_type: feedback_type 参数。
            task_id: task_id 参数。
            conversation_id: conversation_id 参数。
            retrieval_trace_id: retrieval_trace_id 参数。
        """
        feedback = MemoryFeedback(
            memory_id=memory_id,
            user_id=user_id,
            feedback_type=feedback_type,
            task_id=task_id,
            conversation_id=conversation_id,
            retrieval_trace_id=retrieval_trace_id,
        )
        self.session.add(feedback)
        await self.session.flush()
        return feedback

    async def get_active_memory_by_user(
        self,
        *,
        memory_id: str,
        user_id: str,
    ) -> Memory | None:
        """获取 active memory by user。

        Args:
            memory_id: memory_id 参数。
            user_id: user_id 参数。
        """
        now = utc_now()
        return await self.session.scalar(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id == user_id,
                *eligible_memory_conditions(now),
            )
        )


def eligible_memory_conditions(now: datetime) -> tuple[ColumnElement[bool], ...]:
    """处理 eligible memory conditions。

    Args:
        now: now 参数。
    """
    return (
        Memory.is_active.is_(True),
        Memory.status == "active",
        Memory.deleted_at.is_(None),
        Memory.archived_at.is_(None),
        or_(Memory.expires_at.is_(None), Memory.expires_at > now),
    )
