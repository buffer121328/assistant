from __future__ import annotations

from typing import Any


def positive_int(value: object, default: int) -> int:
    """Return a positive integer bounded by the configured default.

    Args:
        value: 待校验、归一化或转换的输入值。
        default: 用于执行当前操作的 default 参数。
    """
    try:
        parsed = (
            int(value) if isinstance(value, int | str | bytes | bytearray) else default
        )
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, default))


def safe_arguments(arguments: dict[str, Any]) -> dict[str, object]:
    """Redact sensitive memory-tool arguments before audit logging.

    Args:
        arguments: 当前调用的结构化参数。
    """
    safe = dict(arguments)
    if "content" in safe:
        safe["content"] = "[redacted-memory-content]"
    return safe
