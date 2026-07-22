from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
import json
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.memory.working_set import ConversationCompactionPolicy, estimate_tokens
from model_gateway import sanitize_text

from domain.conversations import ConversationError, ConversationService
from domain.models import (
    ConversationMessage,
    ConversationSummary,
    MemoryBlock,
    utc_now,
)


@dataclass(frozen=True)
class SummaryDraft:
    """表示 处理 summary draft 的后端数据结构或服务对象。"""

    current_goal: str = ""
    confirmed_facts: tuple[str, ...] = ()
    pending_items: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    pending_confirmations: tuple[str, ...] = ()
    discarded_information: tuple[str, ...] = ()

    def safe(self) -> SummaryDraft:
        """处理 safe。"""
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
        """渲染。"""
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
    """表示 处理 conversation summarizer 的后端数据结构或服务对象。"""

    async def summarize(
        self,
        *,
        messages: Sequence[ConversationMessage],
        previous: ConversationSummary | None,
    ) -> SummaryDraft:
        """汇总。

        Args:
            messages: messages 参数。
            previous: previous 参数。
        """
        ...


class HeuristicConversationSummarizer:
    """表示 处理 heuristic conversation summarizer 的后端数据结构或服务对象。"""

    SUMMARY_VERSION = "auto-summary-v1"
    MODEL_VERSION = "heuristic-conversation-summary-v1"

    def __init__(self, *, max_items: int = 5, max_item_chars: int = 160) -> None:
        """初始化对象实例。

        Args:
            max_items: max_items 参数。
            max_item_chars: max_item_chars 参数。
        """
        self.max_items = max(1, max_items)
        self.max_item_chars = max(40, max_item_chars)

    async def summarize(
        self,
        *,
        messages: Sequence[ConversationMessage],
        previous: ConversationSummary | None,
    ) -> SummaryDraft:
        """汇总。

        Args:
            messages: messages 参数。
            previous: previous 参数。
        """
        previous_draft = _draft_from_summary(previous)
        user_messages = tuple(
            _bounded_item(message.content, self.max_item_chars)
            for message in messages
            if message.role == "user" and message.content.strip()
        )
        assistant_messages = tuple(
            _bounded_item(message.content, self.max_item_chars)
            for message in messages
            if message.role == "assistant" and message.content.strip()
        )
        current_goal = (
            user_messages[-1] if user_messages else previous_draft.current_goal
        )
        confirmed_facts = _merge_limited(
            previous_draft.confirmed_facts,
            user_messages[-self.max_items :],
            limit=self.max_items,
        )
        pending_items = _merge_limited(
            previous_draft.pending_items,
            assistant_messages[-self.max_items :],
            limit=self.max_items,
        )
        sources = _merge_limited(
            previous_draft.sources,
            (f"conversation_messages:{messages[0].id}..{messages[-1].id}",)
            if messages
            else (),
            limit=self.max_items,
        )
        return SummaryDraft(
            current_goal=current_goal,
            confirmed_facts=confirmed_facts,
            pending_items=pending_items,
            decisions=previous_draft.decisions[: self.max_items],
            sources=sources,
            pending_confirmations=previous_draft.pending_confirmations[
                : self.max_items
            ],
            discarded_information=previous_draft.discarded_information[
                : self.max_items
            ],
        )


class ConversationMemoryService:
    """表示 处理 conversation memory service 的后端数据结构或服务对象。"""

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
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session
        self.conversations = ConversationService(session)

    async def get_active_summary(
        self, *, conversation_id: str, user_id: str
    ) -> ConversationSummary | None:
        """获取 active summary。

        Args:
            conversation_id: conversation_id 参数。
            user_id: user_id 参数。
        """
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
        """确保 summary current。

        Args:
            conversation_id: conversation_id 参数。
            user_id: user_id 参数。
            summarizer: summarizer 参数。
            policy: policy 参数。
            summary_version: summary_version 参数。
            model_version: model_version 参数。
            exclude_task_id: exclude_task_id 参数。
        """
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
        """执行 处理 summary needs update 的内部辅助逻辑。

        Args:
            messages: messages 参数。
            active: active 参数。
            policy: policy 参数。
        """
        total_tokens = sum(estimate_tokens(message.content) for message in messages)
        crosses_initial_threshold = (
            total_tokens >= policy.trigger_token_threshold
            or len(messages) >= policy.trigger_message_count
        )
        if active is None:
            return crosses_initial_threshold

        if active.source_end_message_id == messages[-1].id:
            return False

        after_summary = _messages_after(messages, active.source_end_message_id)
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
        """更新 summary。

        Args:
            conversation_id: conversation_id 参数。
            user_id: user_id 参数。
            summarizer: summarizer 参数。
            summary_version: summary_version 参数。
            model_version: model_version 参数。
            exclude_task_id: exclude_task_id 参数。
        """
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
        """处理 upsert block。

        Args:
            user_id: user_id 参数。
            block_type: block_type 参数。
            scope_kind: scope_kind 参数。
            scope_id: scope_id 参数。
            content: content 参数。
            character_limit: character_limit 参数。
            token_limit: token_limit 参数。
            read_only: read_only 参数。
            update_policy: update_policy 参数。
            allow_read_only_update: allow_read_only_update 参数。
        """
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
        """列出 blocks。

        Args:
            user_id: user_id 参数。
            conversation_id: conversation_id 参数。
            project_id: project_id 参数。
        """
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
    """执行 处理 safe 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    return " ".join(sanitize_text(value).strip().split())[:20_000]


def _safe_items(values: Sequence[str]) -> tuple[str, ...]:
    """执行 处理 safe items 的内部辅助逻辑。

    Args:
        values: values 参数。
    """
    return tuple(item for value in values if (item := _safe(value)))


def _messages_after(
    messages: Sequence[ConversationMessage], source_end_message_id: str
) -> tuple[ConversationMessage, ...]:
    """执行 处理 messages after 的内部辅助逻辑。

    Args:
        messages: messages 参数。
        source_end_message_id: source_end_message_id 参数。
    """
    for index, message in enumerate(messages):
        if message.id == source_end_message_id:
            return tuple(messages[index + 1 :])
    return tuple(messages)


def _draft_from_summary(summary: ConversationSummary | None) -> SummaryDraft:
    """执行 处理 draft from summary 的内部辅助逻辑。

    Args:
        summary: summary 参数。
    """
    if summary is None:
        return SummaryDraft()
    try:
        raw = json.loads(summary.content_json)
    except json.JSONDecodeError:
        return SummaryDraft(current_goal=summary.summary_text[:300])
    if not isinstance(raw, dict):
        return SummaryDraft(current_goal=summary.summary_text[:300])
    return SummaryDraft(
        current_goal=_safe(str(raw.get("current_goal") or "")),
        confirmed_facts=_tuple_from_json(raw.get("confirmed_facts")),
        pending_items=_tuple_from_json(raw.get("pending_items")),
        decisions=_tuple_from_json(raw.get("decisions")),
        sources=_tuple_from_json(raw.get("sources")),
        pending_confirmations=_tuple_from_json(raw.get("pending_confirmations")),
        discarded_information=_tuple_from_json(raw.get("discarded_information")),
    )


def _tuple_from_json(value: object) -> tuple[str, ...]:
    """执行 处理 tuple from json 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in (_safe(str(item)) for item in value) if item)


def _bounded_item(value: str, limit: int) -> str:
    """执行 处理 bounded item 的内部辅助逻辑。

    Args:
        value: value 参数。
        limit: limit 参数。
    """
    safe = _safe(value)
    if len(safe) <= limit:
        return safe
    return f"{safe[:limit]}..."


def _merge_limited(
    existing: Sequence[str], additions: Sequence[str], *, limit: int
) -> tuple[str, ...]:
    """执行 处理 merge limited 的内部辅助逻辑。

    Args:
        existing: existing 参数。
        additions: additions 参数。
        limit: limit 参数。
    """
    result: list[str] = []
    seen: set[str] = set()
    for value in tuple(existing) + tuple(additions):
        item = _safe(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result[-limit:])
