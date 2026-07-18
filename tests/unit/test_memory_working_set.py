from __future__ import annotations

from agent.memory.working_set import (
    ConversationMessageRef,
    build_context_pack,
    estimate_tokens,
    select_recent_turns,
)


def messages(count: int) -> tuple[ConversationMessageRef, ...]:
    return tuple(
        ConversationMessageRef(
            id=f"message-{index}",
            role="user" if index % 2 else "assistant",
            content=f"第{index}条消息",
        )
        for index in range(1, count + 1)
    )


def test_token_estimator_is_deterministic_and_normalized_by_units() -> None:
    assert estimate_tokens("回答 keep concise") == 4
    assert estimate_tokens("回答 keep concise") == estimate_tokens(
        "回答  keep   concise"
    )


def test_recent_selector_keeps_complete_turns_instead_of_fixed_count() -> None:
    history = messages(30)
    selected, truncated, used = select_recent_turns(history, token_budget=20)

    assert len(history) > 12
    assert selected
    assert selected[0].role == "user"
    assert selected[-1].role == "assistant"
    assert used <= 20
    assert set(truncated).isdisjoint(message.id for message in selected)
    assert tuple(message.id for message in history) == truncated + tuple(
        message.id for message in selected
    )


def test_context_pack_preserves_section_order_budget_and_safe_trace() -> None:
    history = messages(30)
    pack = build_context_pack(
        memory_blocks=(
            ("stable-constraint", "不要自动发送邮件"),
            ("profile", "使用中文"),
        ),
        conversation_summary="目标：完成 V6；已废弃：旧方案",
        summary_source_ids=("message-1", "message-10"),
        summary_version="summary-v2",
        long_term_memory="回答先给结论",
        messages=history,
        current_input="继续实现",
        total_budget=80,
        reserved_tokens=10,
    )

    assert [item.section for item in pack.trace] == [
        "memory_blocks",
        "conversation_summary",
        "long_term_memory",
        "recent_turns",
        "current_input",
        "tool_results",
    ]
    assert pack.total_estimated_tokens <= 70
    assert pack.compacted is True
    assert pack.memory_blocks[0] == "不要自动发送邮件"
    assert pack.trace[1].source_ids == ("message-1", "message-10")
    assert pack.trace[1].version == "summary-v2"
    assert pack.trace[3].truncated_source_ids
    serialized = repr(pack.trace)
    assert "不要自动发送邮件" not in serialized


def test_context_pack_drops_lower_priority_sections_instead_of_exceeding_budget() -> (
    None
):
    pack = build_context_pack(
        memory_blocks=(
            ("stable", "必须保留"),
            ("profile", "这是一个非常长的低优先内容"),
        ),
        conversation_summary="同样很长的摘要内容",
        long_term_memory="长期记忆内容",
        messages=messages(8),
        current_input="继续",
        total_budget=15,
        reserved_tokens=2,
    )

    assert pack.total_estimated_tokens <= 13
    assert pack.memory_blocks == ("必须保留",)
    assert pack.trace[0].truncated_source_ids == ("profile",)
