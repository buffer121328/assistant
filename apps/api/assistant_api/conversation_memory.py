from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
import json
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.memory.working_set import estimate_tokens
from packages.model_gateway import sanitize_text

from .conversations import ConversationError, ConversationService
from .models import (
    ConversationMessage,
    ConversationSummary,
    MemoryBlock,
    utc_now,
)


@dataclass(frozen=True)
class SummaryDraft:
    current_goal: str = ""
    confirmed_facts: tuple[str, ...] = ()
    pending_items: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    pending_confirmations: tuple[str, ...] = ()
    discarded_information: tuple[str, ...] = ()

    def safe(self) -> SummaryDraft:
        return SummaryDraft(
            current_goal=_safe(self.current_goal),
            confirmed_facts=_safe_items(self.confirmed_facts),
            pending_items=_safe_items(self.pending_items),
            decisions=_safe_items(self.decisions),
            sources=_safe_items(self.sources),
            pending_confirmations=_safe_items(self.pending_confirmations),
            discarded_information=_safe_items(self.discarded_information),
        )

    def render(self) -> str:
        sections = (
            ("当前目标", (self.current_goal,) if self.current_goal else ()),
            ("已确认事实", self.confirmed_facts),
            ("未完成事项", self.pending_items),
            ("已作决定", self.decisions),
            ("关键来源", self.sources),
            ("待确认", self.pending_confirmations),
            ("已废弃", self.discarded_information),
        )
        return "\n".join(
            f"{title}: {'；'.join(items)}" for title, items in sections if items
        )


class ConversationSummarizer(Protocol):
    async def summarize(
        self,
        *,
        messages: Sequence[ConversationMessage],
        previous: ConversationSummary | None,
    ) -> SummaryDraft: ...


class ConversationMemoryService:
    BLOCK_TYPES = {
        "human_profile",
        "communication_style",
        "stable_constraints",
        "current_priorities",
        "project_context",
    }
    BLOCK_ORDER = {
        "stable_constraints": 0,
        "human_profile": 1,
        "communication_style": 2,
        "current_priorities": 3,
        "project_context": 4,
    }

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversations = ConversationService(session)

    async def get_active_summary(
        self, *, conversation_id: str, user_id: str
    ) -> ConversationSummary | None:
        await self.conversations.get_owned(
            conversation_id=conversation_id, user_id=user_id
        )
        return await self.session.scalar(
            select(ConversationSummary)
            .where(
                ConversationSummary.conversation_id == conversation_id,
                ConversationSummary.user_id == user_id,
                ConversationSummary.status == "active",
            )
            .order_by(ConversationSummary.updated_at.desc())
            .limit(1)
        )

    async def update_summary(
        self,
        *,
        conversation_id: str,
        user_id: str,
        summarizer: ConversationSummarizer,
        summary_version: str,
        model_version: str,
    ) -> ConversationSummary | None:
        messages = await self.conversations.list_messages(
            conversation_id=conversation_id, user_id=user_id, limit=200
        )
        if not messages:
            raise ConversationError("conversation_summary_empty")
        previous = await self.get_active_summary(
            conversation_id=conversation_id, user_id=user_id
        )
        try:
            draft = (
                await summarizer.summarize(messages=messages, previous=previous)
            ).safe()
        except Exception:
            return None
        summary_text = draft.render()
        if not summary_text:
            return None
        if previous is not None:
            previous.status = "superseded"
        item = ConversationSummary(
            conversation_id=conversation_id,
            user_id=user_id,
            summary_text=summary_text,
            content_json=json.dumps(asdict(draft), ensure_ascii=False, sort_keys=True),
            source_start_message_id=messages[0].id,
            source_end_message_id=messages[-1].id,
            source_message_count=len(messages),
            summary_version=summary_version[:64],
            model_version=model_version[:128],
            status="active",
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def upsert_block(
        self,
        *,
        user_id: str,
        block_type: str,
        scope_kind: str,
        scope_id: str | None,
        content: str,
        character_limit: int = 4_000,
        token_limit: int = 1_000,
        read_only: bool = False,
        update_policy: str = "user_confirmed",
        allow_read_only_update: bool = False,
    ) -> MemoryBlock:
        if block_type not in self.BLOCK_TYPES:
            raise ConversationError("memory_block_type_invalid")
        if scope_kind not in {
            "user/global",
            "user/project",
            "user/conversation",
            "system/read_only",
        }:
            raise ConversationError("memory_block_scope_invalid")
        if scope_kind != "user/global" and not scope_id:
            raise ConversationError("memory_block_scope_id_required")
        safe_content = _safe(content)
        tokens = estimate_tokens(safe_content)
        if (
            not safe_content
            or len(safe_content) > character_limit
            or tokens > token_limit
        ):
            raise ConversationError("memory_block_limit_exceeded")
        existing = await self.session.scalar(
            select(MemoryBlock).where(
                MemoryBlock.user_id == user_id,
                MemoryBlock.block_type == block_type,
                MemoryBlock.scope_kind == scope_kind,
                MemoryBlock.scope_id == scope_id,
            )
        )
        if existing is not None:
            if existing.read_only and not allow_read_only_update:
                raise ConversationError("memory_block_read_only", 409)
            existing.content = safe_content
            existing.estimated_tokens = tokens
            existing.character_limit = character_limit
            existing.token_limit = token_limit
            existing.read_only = read_only
            existing.update_policy = update_policy
            existing.updated_at = utc_now()
            await self.session.flush()
            return existing
        item = MemoryBlock(
            user_id=user_id,
            block_type=block_type,
            scope_kind=scope_kind,
            scope_id=scope_id,
            content=safe_content,
            estimated_tokens=tokens,
            character_limit=character_limit,
            token_limit=token_limit,
            read_only=read_only,
            update_policy=update_policy,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_blocks(
        self,
        *,
        user_id: str,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> list[MemoryBlock]:
        now = utc_now()
        scopes = [
            MemoryBlock.scope_kind == "user/global",
            MemoryBlock.scope_kind == "system/read_only",
        ]
        if conversation_id:
            scopes.append(
                (MemoryBlock.scope_kind == "user/conversation")
                & (MemoryBlock.scope_id == conversation_id)
            )
        if project_id:
            scopes.append(
                (MemoryBlock.scope_kind == "user/project")
                & (MemoryBlock.scope_id == project_id)
            )
        result = list(
            await self.session.scalars(
                select(MemoryBlock).where(
                    MemoryBlock.user_id == user_id,
                    or_(*scopes),
                    or_(MemoryBlock.expires_at.is_(None), MemoryBlock.expires_at > now),
                )
            )
        )
        return sorted(
            result, key=lambda item: (self.BLOCK_ORDER[item.block_type], item.id)
        )


def _safe(value: str) -> str:
    return " ".join(sanitize_text(value).strip().split())[:20_000]


def _safe_items(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(item for value in values if (item := _safe(value)))
