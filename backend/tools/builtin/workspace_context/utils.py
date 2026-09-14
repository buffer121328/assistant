from __future__ import annotations

from typing import Any

from .constants import DEFAULT_DENY_GLOBS


def parse_deny_globs(
    value: str | tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    """解析 deny globs。

    Args:
        value: value 参数。
    """
    if value is None:
        return DEFAULT_DENY_GLOBS
    if isinstance(value, str):
        items = tuple(item.strip() for item in value.split(",") if item.strip())
    else:
        items = tuple(str(item).strip() for item in value if str(item).strip())
    return items or DEFAULT_DENY_GLOBS


def _optional_int(value: Any) -> int | None:
    """执行 处理 optional int 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if value is None:
        return None
    return int(value)


def _compact(value: str, limit: int) -> str:
    """执行 处理 compact 的内部辅助逻辑。

    Args:
        value: value 参数。
        limit: limit 参数。
    """
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."
