from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from model_gateway import GatewayMessage, GatewayRequest, ModelGatewayError, route_model
from model_gateway.pool_factory import build_pooled_model_gateway

from infrastructure.config import Settings

CORE_INTENT_TASK_TYPES = frozenset({"plan", "learn", "daily", "office"})
UTILITY_COMMAND_TASK_TYPES = frozenset({"memory", "status"})
ALL_COMMAND_TASK_TYPES = CORE_INTENT_TASK_TYPES | UTILITY_COMMAND_TASK_TYPES
LANGBOT_INTENT_OUTCOMES = Literal[
    "plan",
    "learn",
    "daily",
    "office",
    "needs_confirmation",
    "needs_new_capability",
]


class LangBotIntentDecision(BaseModel):
    """表示 处理 lang bot intent decision 的后端数据结构或服务对象。"""

    outcome: LANGBOT_INTENT_OUTCOMES
    reason: str = Field(min_length=1)

    @property
    def task_type(self) -> str | None:
        """处理 task type。"""
        if self.outcome in CORE_INTENT_TASK_TYPES:
            return self.outcome
        return None


async def classify_langbot_intent(
    text: str,
    *,
    settings: Settings,
) -> LangBotIntentDecision:
    """处理 classify langbot intent。

    Args:
        text: text 参数。
        settings: settings 参数。
    """
    normalized = text.strip()
    if not normalized:
        return LangBotIntentDecision(
            outcome="needs_confirmation",
            reason="empty_text",
        )

    command = normalized.split(maxsplit=1)[0]
    if command.startswith("/") and command not in {
        "/plan",
        "/learn",
        "/daily",
        "/office",
        "/memory",
        "/status",
    }:
        return LangBotIntentDecision(
            outcome="needs_new_capability",
            reason=f"unsupported_command:{command}",
        )

    try:
        adapter = build_pooled_model_gateway(settings)
        gateway_request = GatewayRequest(
            user_id="langbot-intent-router",
            task_id="langbot-intent-router",
            task_type="router",
            model_class=None,
            messages=(
                GatewayMessage(
                    role="system",
                    content=(
                        "You are a LangBot intent router. "
                        "Choose exactly one outcome from: plan, learn, daily, office, "
                        "needs_confirmation, needs_new_capability. "
                        "Return JSON only with keys outcome and reason. "
                        "Use needs_confirmation when the request could map to an existing "
                        "core intent but is too ambiguous to trust. "
                        "Use needs_new_capability when the request is outside the supported "
                        "core intent set."
                    ),
                ),
                GatewayMessage(role="user", content=normalized),
            ),
            temperature=0.0,
            max_tokens=256,
        )
        resolved_model_class = route_model(
            gateway_request.task_type,
            gateway_request.model_class,
        )
        result = await adapter.chat(gateway_request, resolved_model_class)
        decision = LangBotIntentDecision.model_validate_json(result.content)
    except (ModelGatewayError, ValueError, json.JSONDecodeError, TypeError):
        return LangBotIntentDecision(
            outcome="needs_confirmation",
            reason="classifier_unavailable",
        )
    except Exception:
        return LangBotIntentDecision(
            outcome="needs_confirmation",
            reason="classifier_unavailable",
        )

    if decision.outcome in CORE_INTENT_TASK_TYPES:
        return LangBotIntentDecision(
            outcome=decision.outcome,
            reason=decision.reason,
        )
    if decision.outcome in {"needs_confirmation", "needs_new_capability"}:
        return decision

    return LangBotIntentDecision(
        outcome="needs_confirmation",
        reason="classifier_invalid_outcome",
    )
