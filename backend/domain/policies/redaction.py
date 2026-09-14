from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

SENSITIVE_PATTERNS = (
    re.compile(r"Bearer\s+(?:\[REDACTED\]|[A-Za-z0-9._~+/=-]+)", re.IGNORECASE),
    re.compile(r"(?i)\"?(authorization|cookie)\"?\s*[:=]\s*\"?[^,}\"']+"),
    re.compile(r"(?i)\"?(api[_-]?key|token|secret)\"?\s*[:=]\s*\"?[^,\s}\"']+"),
    re.compile(r"https?://private\.[^\s}\"')]+", re.IGNORECASE),
)


def sanitize_text(
    value: Any,
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> str:
    """Redact known secret-like values from text before logging or display.

    Args:
        value: 待校验、归一化或转换的输入值。
        extra_sensitive_values: 用于执行当前操作的 extra sensitive values 参数。
    """
    text = str(value)
    for sensitive_value in extra_sensitive_values:
        if sensitive_value:
            text = text.replace(sensitive_value, "[REDACTED]")
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
