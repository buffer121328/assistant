from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from memory import (
    NoopSemanticMemory,
    SemanticMemory,
    classify_memory_sensitivity,
    memory_content_hash,
    normalize_memory_content,
)
from domain.models import (
    Memory,
    MemoryFeedback,
    MemoryIndexOutbox,
    MemoryLink,
    utc_now,
)
from infrastructure.repositories import (
    MemoryCreate,
    MemoryRepository,
    TaskRepository,
)

from .commands import MemoryCommandMixin
from .errors import (
    ForbiddenMemoryContentError,
    InvalidMemoryCommandError,
    MemoryNotFoundError,
)
from .semantic import SemanticMemorySyncMixin


class MemoryService(MemoryCommandMixin, SemanticMemorySyncMixin):
    """表示 处理 memory service 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        semantic_memory: SemanticMemory | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            semantic_memory: semantic_memory 参数。
        """
        self.session = session
        self.repository = MemoryRepository(session)
        self.task_repository = TaskRepository(session)
        self.semantic_memory = semantic_memory or NoopSemanticMemory()

    async def create_memory(
        self,
        *,
        user_id: str,
        content: str,
        memory_type: str = "preference",
        source_kind: str = "explicit_service",
        source_trust: str = "trusted_user",
        source_spans_json: str = "[]",
        candidate_links_json: str = "[]",
        reason_code: str = "explicit_user_request",
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        source_task_id: str | None = None,
        supersedes_id: str | None = None,
        confirmed_by_user: bool = True,
    ) -> Memory:
        """创建 memory。

        Args:
            user_id: user_id 参数。
            content: content 参数。
            memory_type: memory_type 参数。
            source_kind: source_kind 参数。
            source_trust: source_trust 参数。
            source_spans_json: source_spans_json 参数。
            candidate_links_json: candidate_links_json 参数。
            reason_code: reason_code 参数。
            source_conversation_id: source_conversation_id 参数。
            source_message_id: source_message_id 参数。
            source_task_id: source_task_id 参数。
            supersedes_id: supersedes_id 参数。
            confirmed_by_user: confirmed_by_user 参数。
        """
        normalized_content = normalize_memory_content(content)
        if not normalized_content:
            raise InvalidMemoryCommandError("记忆内容不能为空")
        safety = classify_memory_sensitivity(normalized_content)
        if safety.sensitivity == "forbidden":
            raise ForbiddenMemoryContentError("记忆内容包含禁止保存的凭据类型")
        if source_message_id is not None:
            existing = await self.repository.get_by_source_message(
                user_id=user_id,
                source_kind=source_kind,
                source_message_id=source_message_id,
            )
            if existing is not None:
                return existing
        now = utc_now()
        return await self.repository.create_memory(
            MemoryCreate(
                user_id=user_id,
                content=normalized_content,
                normalized_content=normalized_content,
                content_hash=memory_content_hash(normalized_content),
                memory_type=memory_type,
                status="active" if confirmed_by_user else "candidate",
                sensitivity=safety.sensitivity,
                confirmed_by_user=confirmed_by_user,
                confirmed_at=now if confirmed_by_user else None,
                source_kind=source_kind,
                source_trust=source_trust,
                source_spans_json=source_spans_json,
                candidate_links_json=candidate_links_json,
                reason_code=reason_code,
                source_conversation_id=source_conversation_id,
                source_message_id=source_message_id,
                source_task_id=source_task_id,
                supersedes_id=supersedes_id,
            )
        )

    async def list_active_memories(self, user_id: str) -> list[Memory]:
        """列出 active memories。

        Args:
            user_id: user_id 参数。
        """
        return await self.repository.list_active_memories(user_id)

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
        return await self.repository.list_memories(
            user_id=user_id, status=status, scope_kind=scope_kind
        )

    async def get_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """获取 memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.repository.get_memory_by_user(
            user_id=user_id, memory_id=memory_id
        )
        if memory is None:
            raise MemoryNotFoundError("未找到记忆或无权访问")
        return memory

    async def confirm_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """处理 confirm memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        if memory.status not in {"candidate", "conflict_pending"}:
            raise InvalidMemoryCommandError("当前记忆状态不可确认")
        now = utc_now()
        memory.status = "active"
        memory.is_active = True
        memory.confirmed_by_user = True
        memory.confirmed_at = now
        memory.valid_from = memory.valid_from or now
        if memory.supersedes_id is not None:
            old = await self.get_memory(user_id=user_id, memory_id=memory.supersedes_id)
            old.status = "superseded"
            old.is_active = False
            old.valid_to = now
            await self.repository.create_link(
                source_memory_id=memory.id,
                target_memory_id=old.id,
                link_type="supersedes",
                created_by="user",
            )
        await self.session.flush()
        return memory

    async def reject_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """拒绝 memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        if memory.status not in {"candidate", "conflict_pending"}:
            raise InvalidMemoryCommandError("当前记忆状态不可拒绝")
        memory.status = "rejected"
        memory.is_active = False
        await self.session.flush()
        return memory

    async def correct_memory(
        self,
        *,
        user_id: str,
        memory_id: str,
        content: str,
        confirm: bool = False,
    ) -> Memory:
        """处理 correct memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
            content: content 参数。
            confirm: confirm 参数。
        """
        original = await self.get_memory(user_id=user_id, memory_id=memory_id)
        if original.status not in {"active", "candidate", "conflict_pending"}:
            raise InvalidMemoryCommandError("当前记忆状态不可修正")
        corrected = await self.create_memory(
            user_id=user_id,
            content=content,
            memory_type=original.memory_type,
            source_kind="user_correction",
            confirmed_by_user=False,
        )
        corrected.scope_kind = original.scope_kind
        corrected.scope_id = original.scope_id
        corrected.supersedes_id = original.id
        await self.session.flush()
        if confirm:
            return await self.confirm_memory(user_id=user_id, memory_id=corrected.id)
        return corrected

    async def archive_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """归档 memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        memory.status = "archived"
        memory.is_active = False
        memory.archived_at = utc_now()
        await self.session.flush()
        return memory

    async def set_memory_pinned(
        self, *, user_id: str, memory_id: str, pinned: bool
    ) -> Memory:
        """处理 set memory pinned。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
            pinned: pinned 参数。
        """
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        memory.is_pinned = pinned
        await self.session.flush()
        return memory

    async def change_memory_scope(
        self,
        *,
        user_id: str,
        memory_id: str,
        scope_kind: str,
        scope_id: str | None = None,
    ) -> Memory:
        """处理 change memory scope。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
            scope_kind: scope_kind 参数。
            scope_id: scope_id 参数。
        """
        allowed = {
            "user/global",
            "user/project",
            "user/conversation",
            "agent/profile",
        }
        if scope_kind not in allowed or (scope_kind != "user/global" and not scope_id):
            raise InvalidMemoryCommandError("无效的记忆作用域")
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        memory.scope_kind = scope_kind
        memory.scope_id = None if scope_kind == "user/global" else scope_id
        await self.session.flush()
        return memory

    async def add_feedback(
        self,
        *,
        user_id: str,
        memory_id: str,
        feedback_type: str,
        task_id: str | None = None,
        conversation_id: str | None = None,
        retrieval_trace_id: str | None = None,
    ) -> MemoryFeedback:
        """处理 add feedback。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
            feedback_type: feedback_type 参数。
            task_id: task_id 参数。
            conversation_id: conversation_id 参数。
            retrieval_trace_id: retrieval_trace_id 参数。
        """
        allowed = {
            "helpful",
            "harmful",
            "incorrect",
            "confirmed",
            "scope_changed",
            "forgotten",
        }
        if feedback_type not in allowed:
            raise InvalidMemoryCommandError("无效的记忆反馈类型")
        await self.get_memory(user_id=user_id, memory_id=memory_id)
        return await self.repository.create_feedback(
            memory_id=memory_id,
            user_id=user_id,
            feedback_type=feedback_type,
            task_id=task_id,
            conversation_id=conversation_id,
            retrieval_trace_id=retrieval_trace_id,
        )

    async def add_link(
        self,
        *,
        user_id: str,
        source_memory_id: str,
        target_memory_id: str,
        link_type: str,
        created_by: str = "user",
    ) -> MemoryLink:
        """处理 add link。

        Args:
            user_id: user_id 参数。
            source_memory_id: source_memory_id 参数。
            target_memory_id: target_memory_id 参数。
            link_type: link_type 参数。
            created_by: created_by 参数。
        """
        allowed_links = {
            "related_to",
            "derived_from",
            "supports",
            "contradicts",
            "supersedes",
            "part_of",
            "applies_to_project",
        }
        if link_type not in allowed_links:
            raise InvalidMemoryCommandError("无效的记忆链接类型")
        await self.get_memory(user_id=user_id, memory_id=source_memory_id)
        await self.get_memory(user_id=user_id, memory_id=target_memory_id)
        return await self.repository.create_link(
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            link_type=link_type,
            created_by=created_by,
        )

    async def list_links(self, *, user_id: str, memory_id: str) -> list[MemoryLink]:
        """列出 links。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        await self.get_memory(user_id=user_id, memory_id=memory_id)
        return await self.repository.list_links_for_memory(memory_id=memory_id)

    async def list_feedback(
        self, *, user_id: str, memory_id: str
    ) -> list[MemoryFeedback]:
        """列出 feedback。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        await self.get_memory(user_id=user_id, memory_id=memory_id)
        return await self.repository.list_feedback_for_memory(
            memory_id=memory_id, user_id=user_id
        )

    async def list_index_outbox(
        self, *, user_id: str, status: str | None = None
    ) -> list[MemoryIndexOutbox]:
        """列出 index outbox。

        Args:
            user_id: user_id 参数。
            status: status 参数。
        """
        return await self.repository.list_index_outbox(user_id=user_id, status=status)

    async def forget_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """处理 forget memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        memory.status = "deleted"
        memory.is_active = False
        memory.deleted_at = utc_now()
        await self.repository.create_feedback(
            memory_id=memory.id,
            user_id=user_id,
            feedback_type="forgotten",
        )
        await self.session.flush()
        return memory

    async def delete_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """删除 memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.repository.get_active_memory_by_user(
            memory_id=memory_id,
            user_id=user_id,
        )
        if memory is None:
            raise MemoryNotFoundError("未找到可删除的记忆或无权访问")

        memory.is_active = False
        memory.status = "deleted"
        memory.deleted_at = utc_now()
        await self.session.flush()
        return memory
