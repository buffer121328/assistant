from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
from typing import Protocol

from session.text import (
    bounded_item,
    merge_limited,
    safe_items,
    safe_text,
)
from domain.models import ConversationMessage, ConversationSummary


@dataclass(frozen=True)
class SummaryDraft:
    """Structured draft used to render and persist a conversation summary."""

    current_goal: str = ""
    confirmed_facts: tuple[str, ...] = ()
    pending_items: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    pending_confirmations: tuple[str, ...] = ()
    discarded_information: tuple[str, ...] = ()

    def safe(self) -> SummaryDraft:
        """Return a sanitized copy safe for storage."""
        return SummaryDraft(
            current_goal=safe_text(self.current_goal),
            confirmed_facts=safe_items(self.confirmed_facts),
            pending_items=safe_items(self.pending_items),
            decisions=safe_items(self.decisions),
            sources=safe_items(self.sources),
            pending_confirmations=safe_items(self.pending_confirmations),
            discarded_information=safe_items(self.discarded_information),
        )

    def render(self) -> str:
        """Render a compact human-readable summary."""
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
    """Protocol for conversation summary generators."""

    async def summarize(
        self,
        *,
        messages: Sequence[ConversationMessage],
        previous: ConversationSummary | None,
    ) -> SummaryDraft:
        """Summarize source messages into a structured draft."""
        ...


class HeuristicConversationSummarizer:
    """Deterministic fallback summarizer for short-term memory compaction."""

    SUMMARY_VERSION = "auto-summary-v1"
    MODEL_VERSION = "heuristic-conversation-summary-v1"

    def __init__(self, *, max_items: int = 5, max_item_chars: int = 160) -> None:
        self.max_items = max(1, max_items)
        self.max_item_chars = max(40, max_item_chars)

    async def summarize(
        self,
        *,
        messages: Sequence[ConversationMessage],
        previous: ConversationSummary | None,
    ) -> SummaryDraft:
        """Summarize messages by keeping bounded recent user and assistant items."""
        previous_draft = draft_from_summary(previous)
        user_messages = tuple(
            bounded_item(message.content, self.max_item_chars)
            for message in messages
            if message.role == "user" and message.content.strip()
        )
        assistant_messages = tuple(
            bounded_item(message.content, self.max_item_chars)
            for message in messages
            if message.role == "assistant" and message.content.strip()
        )
        current_goal = (
            user_messages[-1] if user_messages else previous_draft.current_goal
        )
        confirmed_facts = merge_limited(
            previous_draft.confirmed_facts,
            user_messages[-self.max_items :],
            limit=self.max_items,
        )
        pending_items = merge_limited(
            previous_draft.pending_items,
            assistant_messages[-self.max_items :],
            limit=self.max_items,
        )
        sources = merge_limited(
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


def messages_after(
    messages: Sequence[ConversationMessage], source_end_message_id: str
) -> tuple[ConversationMessage, ...]:
    """Return messages after the summary's source end cursor."""
    for index, message in enumerate(messages):
        if message.id == source_end_message_id:
            return tuple(messages[index + 1 :])
    return tuple(messages)


def draft_from_summary(summary: ConversationSummary | None) -> SummaryDraft:
    """Rehydrate a structured draft from a persisted summary row."""
    if summary is None:
        return SummaryDraft()
    try:
        raw = json.loads(summary.content_json)
    except json.JSONDecodeError:
        return SummaryDraft(current_goal=summary.summary_text[:300])
    if not isinstance(raw, dict):
        return SummaryDraft(current_goal=summary.summary_text[:300])
    return SummaryDraft(
        current_goal=safe_text(str(raw.get("current_goal") or "")),
        confirmed_facts=tuple_from_json(raw.get("confirmed_facts")),
        pending_items=tuple_from_json(raw.get("pending_items")),
        decisions=tuple_from_json(raw.get("decisions")),
        sources=tuple_from_json(raw.get("sources")),
        pending_confirmations=tuple_from_json(raw.get("pending_confirmations")),
        discarded_information=tuple_from_json(raw.get("discarded_information")),
    )


def tuple_from_json(value: object) -> tuple[str, ...]:
    """Normalize a JSON list-like value into sanitized text items."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in (safe_text(str(item)) for item in value) if item)
