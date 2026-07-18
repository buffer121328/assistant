from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model_gateway import sanitize_text

from domain.models import Conversation, ConversationMessage, User, utc_now


class ConversationError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: str,
        title: str | None = None,
        channel: str = "desktop",
        external_key: str | None = None,
        commit: bool = True,
    ) -> Conversation:
        if await self.session.get(User, user_id) is None:
            raise ConversationError("conversation_user_not_found", 404)
        safe_title = _title(title or "新会话")
        conversation = Conversation(
            user_id=user_id,
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
        conditions = [
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        ]
        if active_only:
            conditions.append(Conversation.archived_at.is_(None))
        item = await self.session.scalar(select(Conversation).where(*conditions))
        if item is None:
            raise ConversationError("conversation_not_found", 404)
        return item

    async def archive(self, *, conversation_id: str, user_id: str) -> Conversation:
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
        await self.get_owned(conversation_id=conversation_id, user_id=user_id)
        conditions = [ConversationMessage.conversation_id == conversation_id]
        if exclude_task_id:
            conditions.append(ConversationMessage.task_id != exclude_task_id)
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


def _title(value: str) -> str:
    safe = " ".join(sanitize_text(value).strip().split())[:80]
    return safe or "新会话"
