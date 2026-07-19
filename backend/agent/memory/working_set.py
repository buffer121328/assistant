from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]")
SectionName = Literal[
    "memory_blocks",
    "conversation_summary",
    "long_term_memory",
    "recent_turns",
    "current_input",
    "tool_results",
]


@dataclass(frozen=True)
class ConversationCompactionPolicy:
    enabled: bool = True
    trigger_token_threshold: int = 3_000
    trigger_message_count: int = 48
    stale_after_tokens: int = 750
    stale_after_messages: int = 12
    max_source_messages: int = 200

    def __post_init__(self) -> None:
        if self.trigger_token_threshold < 1:
            raise ValueError("trigger_token_threshold must be positive")
        if self.trigger_message_count < 1:
            raise ValueError("trigger_message_count must be positive")
        if self.stale_after_tokens < 1:
            raise ValueError("stale_after_tokens must be positive")
        if self.stale_after_messages < 1:
            raise ValueError("stale_after_messages must be positive")
        if self.max_source_messages < 1:
            raise ValueError("max_source_messages must be positive")


@dataclass(frozen=True)
class ConversationMessageRef:
    id: str
    role: str
    content: str


@dataclass(frozen=True)
class ContextSectionTrace:
    section: SectionName
    estimated_tokens: int
    source_ids: tuple[str, ...] = ()
    truncated_source_ids: tuple[str, ...] = ()
    version: str | None = None


@dataclass(frozen=True)
class ContextPack:
    memory_blocks: tuple[str, ...]
    conversation_summary: str
    long_term_memory: str
    recent_turns: tuple[ConversationMessageRef, ...]
    current_input: str
    tool_results: tuple[str, ...]
    trace: tuple[ContextSectionTrace, ...]
    total_estimated_tokens: int
    compacted: bool


def estimate_tokens(text: str) -> int:
    return len(_TOKEN_PATTERN.findall(text))


def select_recent_turns(
    messages: tuple[ConversationMessageRef, ...], *, token_budget: int
) -> tuple[tuple[ConversationMessageRef, ...], tuple[str, ...], int]:
    if token_budget < 0:
        raise ValueError("token_budget must be non-negative")
    turns = _group_turns(messages)
    selected_reversed: list[tuple[ConversationMessageRef, ...]] = []
    used = 0
    for turn in reversed(turns):
        turn_tokens = sum(estimate_tokens(message.content) for message in turn)
        if used + turn_tokens > token_budget:
            break
        selected_reversed.append(turn)
        used += turn_tokens
    selected_turns = tuple(reversed(selected_reversed))
    selected = tuple(message for turn in selected_turns for message in turn)
    selected_ids = {message.id for message in selected}
    truncated = tuple(
        message.id for message in messages if message.id not in selected_ids
    )
    return selected, truncated, used


def build_context_pack(
    *,
    memory_blocks: tuple[tuple[str, str], ...] = (),
    conversation_summary: str = "",
    summary_source_ids: tuple[str, ...] = (),
    summary_version: str | None = None,
    long_term_memory: str = "",
    messages: tuple[ConversationMessageRef, ...] = (),
    current_input: str,
    tool_results: tuple[str, ...] = (),
    total_budget: int = 4_000,
    reserved_tokens: int = 256,
) -> ContextPack:
    if total_budget <= 0 or reserved_tokens < 0 or reserved_tokens >= total_budget:
        raise ValueError("context budgets must be positive")
    available = total_budget - reserved_tokens
    current_tokens = estimate_tokens(current_input)
    tool_tokens = sum(estimate_tokens(item) for item in tool_results)
    mandatory_tokens = current_tokens + tool_tokens
    if mandatory_tokens > available:
        raise ValueError("current input exceeds context budget")

    remaining = available - mandatory_tokens
    selected_blocks: list[tuple[str, str]] = []
    dropped_block_ids: list[str] = []
    block_tokens = 0
    for block_id, content in memory_blocks:
        tokens = estimate_tokens(content)
        if tokens <= remaining:
            selected_blocks.append((block_id, content))
            block_tokens += tokens
            remaining -= tokens
        else:
            dropped_block_ids.append(block_id)

    summary_tokens = estimate_tokens(conversation_summary)
    selected_summary = conversation_summary if summary_tokens <= remaining else ""
    if selected_summary:
        remaining -= summary_tokens
    else:
        summary_tokens = 0

    long_term_tokens = estimate_tokens(long_term_memory)
    selected_long_term = long_term_memory if long_term_tokens <= remaining else ""
    if selected_long_term:
        remaining -= long_term_tokens
    else:
        long_term_tokens = 0

    recent, truncated_ids, recent_tokens = select_recent_turns(
        messages, token_budget=remaining
    )
    trace = (
        ContextSectionTrace(
            "memory_blocks",
            block_tokens,
            tuple(block_id for block_id, _ in selected_blocks),
            tuple(dropped_block_ids),
        ),
        ContextSectionTrace(
            "conversation_summary",
            summary_tokens,
            summary_source_ids if selected_summary else (),
            summary_source_ids if conversation_summary and not selected_summary else (),
            version=summary_version if selected_summary else None,
        ),
        ContextSectionTrace("long_term_memory", long_term_tokens),
        ContextSectionTrace(
            "recent_turns",
            recent_tokens,
            tuple(message.id for message in recent),
            truncated_ids,
        ),
        ContextSectionTrace("current_input", current_tokens),
        ContextSectionTrace("tool_results", tool_tokens),
    )
    total = sum(section.estimated_tokens for section in trace)
    return ContextPack(
        memory_blocks=tuple(content for _, content in selected_blocks),
        conversation_summary=selected_summary,
        long_term_memory=selected_long_term,
        recent_turns=recent,
        current_input=current_input,
        tool_results=tool_results,
        trace=trace,
        total_estimated_tokens=total,
        compacted=bool(
            truncated_ids
            or conversation_summary
            or dropped_block_ids
            or (long_term_memory and not selected_long_term)
        ),
    )


def _group_turns(
    messages: tuple[ConversationMessageRef, ...],
) -> tuple[tuple[ConversationMessageRef, ...], ...]:
    turns: list[list[ConversationMessageRef]] = []
    for message in messages:
        if message.role == "user" or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return tuple(tuple(turn) for turn in turns)
