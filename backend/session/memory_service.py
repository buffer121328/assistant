from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
import json

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from session.memory_blocks import (
    BLOCK_ORDER,
    BLOCK_TYPES,
    validate_block_content,
    validate_block_identity,
)
from session.summary import (
    ConversationSummarizer,
    HeuristicConversationSummarizer,
    messages_after,
)
from session.text import safe_text
from session.conversations import ConversationError, ConversationService
from domain.models import ConversationMessage, ConversationSummary, MemoryBlock, utc_now
from memory.working_set import ConversationCompactionPolicy, estimate_tokens


class ConversationMemoryService:
    """Application service for conversation summaries and memory blocks."""

    BLOCK_TYPES = BLOCK_TYPES
    BLOCK_ORDER = BLOCK_ORDER

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversations = ConversationService(session)

    async def get_active_summary(
        self, *, conversation_id: str, user_id: str
    ) -> ConversationSummary | None:
        """Return the latest active summary for an owned conversation."""
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

    async def ensure_summary_current(
        self,
        *,
        conversation_id: str,
        user_id: str,
        summarizer: ConversationSummarizer | None = None,
        policy: ConversationCompactionPolicy | None = None,
        summary_version: str | None = None,
        model_version: str | None = None,
        exclude_task_id: str | None = None,
    ) -> ConversationSummary | None:
        """Update the active summary when the compaction policy says it is stale."""
        active = await self.get_active_summary(
            conversation_id=conversation_id, user_id=user_id
        )
        compaction_policy = policy or ConversationCompactionPolicy()
        if not compaction_policy.enabled:
            return active

        messages = await self.conversations.list_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=compaction_policy.max_source_messages,
            exclude_task_id=exclude_task_id,
        )
        if not messages:
            return active

        should_update = self._summary_needs_update(
            messages=messages,
            active=active,
            policy=compaction_policy,
        )
        if not should_update:
            return active

        summarizer = summarizer or HeuristicConversationSummarizer()
        resolved_summary_version = summary_version or str(
            getattr(
                summarizer,
                "SUMMARY_VERSION",
                HeuristicConversationSummarizer.SUMMARY_VERSION,
            )
        )
        resolved_model_version = model_version or str(
            getattr(
                summarizer,
                "MODEL_VERSION",
                HeuristicConversationSummarizer.MODEL_VERSION,
            )
        )
        updated = await self.update_summary(
            conversation_id=conversation_id,
            user_id=user_id,
            summarizer=summarizer,
            summary_version=resolved_summary_version,
            model_version=resolved_model_version,
            exclude_task_id=exclude_task_id,
        )
        return updated or active

    def _summary_needs_update(
        self,
        *,
        messages: Sequence[ConversationMessage],
        active: ConversationSummary | None,
        policy: ConversationCompactionPolicy,
    ) -> bool:
        """Decide whether an active summary should be created or refreshed."""
        total_tokens = sum(estimate_tokens(message.content) for message in messages)
        crosses_initial_threshold = (
            total_tokens >= policy.trigger_token_threshold
            or len(messages) >= policy.trigger_message_count
        )
        if active is None:
            return crosses_initial_threshold

        if active.source_end_message_id == messages[-1].id:
            return False

        after_summary = messages_after(messages, active.source_end_message_id)
        if not after_summary:
            return crosses_initial_threshold
        after_tokens = sum(
            estimate_tokens(message.content) for message in after_summary
        )
        return (
            len(after_summary) >= policy.stale_after_messages
            or after_tokens >= policy.stale_after_tokens
        )

    async def update_summary(
        self,
        *,
        conversation_id: str,
        user_id: str,
        summarizer: ConversationSummarizer,
        summary_version: str,
        model_version: str,
        exclude_task_id: str | None = None,
    ) -> ConversationSummary | None:
        """Create a new active summary from conversation messages."""
        messages = await self.conversations.list_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=200,
            exclude_task_id=exclude_task_id,
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
        """Create or update a scoped memory block."""
        validate_block_identity(
            block_type=block_type, scope_kind=scope_kind, scope_id=scope_id
        )
        safe_content = safe_text(content)
        tokens = validate_block_content(
            safe_content, character_limit=character_limit, token_limit=token_limit
        )
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
        """List active memory blocks visible to a user/conversation/project scope."""
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
