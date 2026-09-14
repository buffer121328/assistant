from __future__ import annotations


from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from memory.working_set import estimate_tokens
from domain.policies.redaction import sanitize_text

from domain.models import (
    Approval,
    ArtifactRecord,
    Conversation,
    ConversationMessage,
    ConversationResourceReference,
    Task,
    User,
    utc_now,
)
from domain.policies.enterprise import LOCAL_ORGANIZATION_ID, LOCAL_TENANT_ID


@dataclass(frozen=True)
class ConversationTokenStats:
    """表示 处理 conversation token stats 的后端数据结构或服务对象。"""

    conversation_id: str  # conversation_id 对应的数据字段。
    message_count: int  # message_count 对应的数据字段。
    user_message_count: int  # user_message_count 对应的数据字段。
    assistant_message_count: int  # assistant_message_count 对应的数据字段。
    total_estimated_tokens: int  # total_estimated_tokens 对应的数据字段。
    user_estimated_tokens: int  # user_estimated_tokens 对应的数据字段。
    assistant_estimated_tokens: int  # assistant_estimated_tokens 对应的数据字段。
    token_limit: int  # token_limit 对应的数据字段。
    used_input_tokens: int  # used_input_tokens 对应的数据字段。
    used_output_tokens: int  # used_output_tokens 对应的数据字段。
    used_total_tokens: int  # used_total_tokens 对应的数据字段。
    reserved_input_tokens: int  # reserved_input_tokens 对应的数据字段。
    reserved_output_tokens: int  # reserved_output_tokens 对应的数据字段。
    reserved_total_tokens: int  # reserved_total_tokens 对应的数据字段。
    remaining_tokens: int  # remaining_tokens 对应的数据字段。
    available_tokens: int  # available_tokens 对应的数据字段。
    blocked_reason: str | None  # blocked_reason 对应的数据字段。

    @property
    def usage_ratio(self) -> float:
        """处理 usage ratio。"""
        if self.token_limit <= 0:
            return 0.0
        return min(1.0, self.used_total_tokens / self.token_limit)

    @property
    def status(self) -> str:
        """处理 status。"""
        if self.blocked_reason or self.available_tokens <= 0 or self.usage_ratio >= 0.9:
            return "full"
        if self.usage_ratio >= 0.7:
            return "warning"
        return "ok"


@dataclass(frozen=True)
class ConversationWorkSummary:
    """定义当前组件的职责和边界。"""

    conversation_id: str  # conversation_id 对应的数据字段。
    scope_kind: str  # scope_kind 对应的数据字段。
    scope_name: str  # scope_name 对应的数据字段。
    space_id: str | None  # space_id 对应的数据字段。
    context_count: int  # context_count 对应的数据字段。
    artifact_count: int  # artifact_count 对应的数据字段。
    pending_approval_count: int  # pending_approval_count 对应的数据字段。


class ConversationError(RuntimeError):
    """表示 处理 conversation error 的后端数据结构或服务对象。"""

    def __init__(self, code: str, status_code: int = 400) -> None:
        """初始化对象实例。

        Args:
            code: code 参数。
            status_code: status_code 参数。
        """
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class ConversationService:
    """表示 处理 conversation service 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def create(
        self,
        *,
        user_id: str,
        title: str | None = None,
        channel: str = "desktop",
        external_key: str | None = None,
        space_id: str | None = None,
        commit: bool = True,
    ) -> Conversation:
        """创建。

        Args:
            user_id: user_id 参数。
            title: title 参数。
            channel: channel 参数。
            external_key: external_key 参数。
            space_id: Optional governed Space binding.
            commit: commit 参数。
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise ConversationError("conversation_user_not_found", 404)
        organization_id = (
            LOCAL_ORGANIZATION_ID if user.tenant_id == LOCAL_TENANT_ID else None
        )
        if space_id is not None:
            from application.session_context.spaces import SpaceService

            space = await SpaceService(self.session).require_edit_access(
                space_id, user_id
            )
            organization_id = space.organization_id
        safe_title = _title(title or "新会话")
        conversation = Conversation(
            user_id=user_id,
            tenant_id=user.tenant_id,
            organization_id=organization_id,
            space_id=space_id,
            owner_type="user",
            owner_id=user_id,
            visibility="private",
            title=safe_title,
            channel=channel.strip()[:32] or "desktop",
            external_key=(external_key.strip()[:512] if external_key else None),
        )
        self.session.add(conversation)
        await self.session.flush()
        if commit:
            await self.session.commit()
            await self.session.refresh(conversation)
        return conversation

    async def resolve_external(
        self, *, user_id: str, channel: str, external_key: str, title: str
    ) -> Conversation:
        """解析 external。

        Args:
            user_id: user_id 参数。
            channel: channel 参数。
            external_key: external_key 参数。
            title: title 参数。
        """
        existing = await self.session.scalar(
            select(Conversation).where(
                Conversation.user_id == user_id,
                Conversation.channel == channel,
                Conversation.external_key == external_key,
            )
        )
        if existing is not None:
            if existing.archived_at is not None:
                existing.archived_at = None
            return existing
        return await self.create(
            user_id=user_id,
            title=title,
            channel=channel,
            external_key=external_key,
            commit=False,
        )

    async def list_active(self, user_id: str, *, limit: int = 50) -> list[Conversation]:
        """列出 active。

        Args:
            user_id: user_id 参数。
            limit: limit 参数。
        """
        if await self.session.get(User, user_id) is None:
            raise ConversationError("conversation_user_not_found", 404)
        items = await self.session.scalars(
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.archived_at.is_(None),
            )
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(items)

    async def get_owned(
        self, *, conversation_id: str, user_id: str, active_only: bool = False
    ) -> Conversation:
        """获取 owned。

        Args:
            conversation_id: conversation_id 参数。
            user_id: user_id 参数。
            active_only: active_only 参数。
        """
        conditions = [
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        ]
        if active_only:
            conditions.append(Conversation.archived_at.is_(None))
        item = await self.session.scalar(select(Conversation).where(*conditions))
        if item is None:
            raise ConversationError("conversation_not_found", 404)
        if active_only and item.space_id is not None:
            from application.session_context.spaces import SpaceError, SpaceService

            try:
                await SpaceService(self.session).require_edit_access(
                    item.space_id, user_id
                )
            except SpaceError as exc:
                raise ConversationError("conversation_not_found", 404) from exc
        return item

    async def set_space(
        self,
        *,
        conversation_id: str,
        user_id: str,
        space_id: str | None,
    ) -> Conversation:
        """Move an owned active Conversation into an eligible Space or Personal Work.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
            space_id: 用于执行当前操作的 space id 参数。
        """
        item = await self.get_owned(conversation_id=conversation_id, user_id=user_id)
        if item.archived_at is not None:
            raise ConversationError("conversation_not_found", 404)
        if space_id is None:
            user = await self.session.get(User, user_id)
            if user is None:
                raise ConversationError("conversation_user_not_found", 404)
            item.space_id = None
            item.organization_id = (
                LOCAL_ORGANIZATION_ID if user.tenant_id == LOCAL_TENANT_ID else None
            )
        else:
            from application.session_context.spaces import SpaceService

            space = await SpaceService(self.session).require_edit_access(
                space_id, user_id
            )
            item.space_id = space.id
            item.organization_id = space.organization_id
        item.updated_at = utc_now()
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def work_summary(
        self, *, conversation_id: str, user_id: str
    ) -> ConversationWorkSummary:
        """Return safe scope and aggregate counts without enumerating inaccessible records.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
        """
        item = await self.get_owned(conversation_id=conversation_id, user_id=user_id)
        if item.archived_at is not None:
            raise ConversationError("conversation_not_found", 404)
        scope_kind = "personal"
        scope_name = "Personal Work"
        if item.space_id is not None:
            from application.session_context.spaces import SpaceError, SpaceService

            try:
                space = await SpaceService(self.session).require_edit_access(
                    item.space_id, user_id
                )
            except SpaceError as exc:
                raise ConversationError("conversation_not_found", 404) from exc
            scope_kind = "space"
            scope_name = space.name
        context_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(ConversationResourceReference)
                .where(
                    ConversationResourceReference.conversation_id == item.id,
                    ConversationResourceReference.user_id == user_id,
                    ConversationResourceReference.status == "attached",
                )
            )
            or 0
        )
        artifact_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(ArtifactRecord)
                .where(
                    ArtifactRecord.conversation_id == item.id,
                    ArtifactRecord.owner_id == user_id,
                    ArtifactRecord.revoked_at.is_(None),
                )
            )
            or 0
        )
        pending_approval_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(Approval)
                .join(Task, Approval.task_id == Task.id)
                .where(
                    Task.conversation_id == item.id,
                    Task.user_id == user_id,
                    Approval.status == "pending",
                )
            )
            or 0
        )
        return ConversationWorkSummary(
            conversation_id=item.id,
            scope_kind=scope_kind,
            scope_name=scope_name,
            space_id=item.space_id,
            context_count=context_count,
            artifact_count=artifact_count,
            pending_approval_count=pending_approval_count,
        )

    async def archive(self, *, conversation_id: str, user_id: str) -> Conversation:
        """归档。

        Args:
            conversation_id: conversation_id 参数。
            user_id: user_id 参数。
        """
        item = await self.get_owned(conversation_id=conversation_id, user_id=user_id)
        item.archived_at = item.archived_at or utc_now()
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def append_message(
        self,
        *,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        task_id: str | None = None,
    ) -> ConversationMessage:
        """处理 append message。

        Args:
            conversation_id: conversation_id 参数。
            user_id: user_id 参数。
            role: role 参数。
            content: content 参数。
            task_id: task_id 参数。
        """
        if role not in {"user", "assistant"}:
            raise ConversationError("conversation_role_invalid")
        conversation = await self.get_owned(
            conversation_id=conversation_id, user_id=user_id
        )
        safe_content = sanitize_text(content).strip()[:20_000]
        if not safe_content:
            raise ConversationError("conversation_message_empty")
        if task_id is not None:
            existing = await self.session.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == conversation_id,
                    ConversationMessage.task_id == task_id,
                    ConversationMessage.role == role,
                    ConversationMessage.content == safe_content,
                )
            )
            if existing is not None:
                return existing
        item = ConversationMessage(
            conversation_id=conversation_id,
            task_id=task_id,
            role=role,
            content=safe_content,
        )
        self.session.add(item)
        conversation.updated_at = utc_now()
        if conversation.title == "新会话" and role == "user":
            conversation.title = _title(safe_content)
        await self.session.flush()
        return item

    async def list_messages(
        self,
        *,
        conversation_id: str,
        user_id: str,
        limit: int = 100,
        exclude_task_id: str | None = None,
    ) -> list[ConversationMessage]:
        """列出 messages。

        Args:
            conversation_id: conversation_id 参数。
            user_id: user_id 参数。
            limit: limit 参数。
            exclude_task_id: exclude_task_id 参数。
        """
        await self.get_owned(conversation_id=conversation_id, user_id=user_id)
        conditions = [ConversationMessage.conversation_id == conversation_id]
        if exclude_task_id:
            conditions.append(
                or_(
                    ConversationMessage.task_id.is_(None),
                    ConversationMessage.task_id != exclude_task_id,
                )
            )
        newest = list(
            await self.session.scalars(
                select(ConversationMessage)
                .where(*conditions)
                .order_by(
                    ConversationMessage.created_at.desc(),
                    ConversationMessage.id.desc(),
                )
                .limit(max(1, min(limit, 200)))
            )
        )
        newest.reverse()
        return newest

    async def token_stats(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ConversationTokenStats:
        """处理 token stats。

        Args:
            conversation_id: conversation_id 参数。
            user_id: user_id 参数。
        """
        from runtime.conversation_budget import ConversationTokenBudget

        messages = await self.list_messages(
            conversation_id=conversation_id, user_id=user_id, limit=200
        )
        budget = await ConversationTokenBudget(self.session).snapshot(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        user_tokens = sum(
            estimate_tokens(message.content)
            for message in messages
            if message.role == "user"
        )
        assistant_tokens = sum(
            estimate_tokens(message.content)
            for message in messages
            if message.role == "assistant"
        )
        return ConversationTokenStats(
            conversation_id=conversation_id,
            message_count=len(messages),
            user_message_count=sum(1 for message in messages if message.role == "user"),
            assistant_message_count=sum(
                1 for message in messages if message.role == "assistant"
            ),
            total_estimated_tokens=user_tokens + assistant_tokens,
            user_estimated_tokens=user_tokens,
            assistant_estimated_tokens=assistant_tokens,
            token_limit=budget.token_limit,
            used_input_tokens=budget.used_input_tokens,
            used_output_tokens=budget.used_output_tokens,
            used_total_tokens=budget.used_total_tokens,
            reserved_input_tokens=budget.reserved_input_tokens,
            reserved_output_tokens=budget.reserved_output_tokens,
            reserved_total_tokens=budget.reserved_total_tokens,
            remaining_tokens=budget.settled_remaining_tokens,
            available_tokens=budget.remaining_tokens,
            blocked_reason=budget.blocked_reason,
        )


def _title(value: str) -> str:
    """执行 处理 title 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    safe = " ".join(sanitize_text(value).strip().split())[:80]
    return safe or "新会话"
