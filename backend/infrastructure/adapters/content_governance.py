from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from agent.governance.content import (
    ContentGuard,
    ContentGuardProviderUnavailableError,
    LocalContentGuard,
    NoopContentGuard,
)

if TYPE_CHECKING:
    from infrastructure.settings.config import Settings


class GuardrailsAIContentGuard(LocalContentGuard):
    """定义当前组件的职责和边界。"""

    provider = "guardrails_ai"

    def __init__(self, *, sensitive_values: tuple[str | None, ...] = ()) -> None:
        """初始化可选 Guardrails AI 适配器及本地脱敏敏感值。

        Args:
            sensitive_values: 用于执行当前操作的 sensitive values 参数。
        """
        try:
            self._guardrails_module = importlib.import_module("guardrails")
        except ImportError as exc:
            raise ContentGuardProviderUnavailableError(
                "The Guardrails AI content-governance provider is unavailable. "
                "Install the optional content-governance dependency group."
            ) from exc
        super().__init__(sensitive_values=sensitive_values)


def build_content_guard(
    settings: Settings,
    *,
    sensitive_values: tuple[str | None, ...] = (),
) -> ContentGuard:
    """Build the configured content boundary without a default network dependency.

    Args:
        settings: 用于执行当前操作的 settings 参数。
        sensitive_values: 用于执行当前操作的 sensitive values 参数。
    """
    if not settings.content_governance_enabled:
        return NoopContentGuard()
    if settings.content_governance_provider == "local":
        return LocalContentGuard(sensitive_values=sensitive_values)
    if settings.content_governance_provider == "guardrails_ai":
        return GuardrailsAIContentGuard(sensitive_values=sensitive_values)
    raise ContentGuardProviderUnavailableError("Unknown content-governance provider")


__all__ = [
    "GuardrailsAIContentGuard",
    "build_content_guard",
]
