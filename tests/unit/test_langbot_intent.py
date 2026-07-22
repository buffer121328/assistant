from __future__ import annotations

import pytest

from infrastructure.config import Settings
from channels.langbot.intent import classify_langbot_intent


@pytest.mark.asyncio
async def test_unknown_slash_command_maps_to_new_capability() -> None:
    decision = await classify_langbot_intent(
        "/unknown test",
        settings=Settings(database_url="sqlite+aiosqlite:///unused.db"),
    )

    assert decision.outcome == "needs_new_capability"
    assert decision.task_type is None


@pytest.mark.asyncio
async def test_classifier_falls_back_to_confirmation_when_model_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_unavailable(_settings: Settings) -> object:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(
        "channels.langbot.intent.build_pooled_models",
        raise_unavailable,
    )

    decision = await classify_langbot_intent(
        "帮我安排一个本周的工作计划",
        settings=Settings(database_url="sqlite+aiosqlite:///unused.db"),
    )

    assert decision.outcome == "needs_confirmation"
    assert decision.task_type is None
