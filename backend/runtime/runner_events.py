from __future__ import annotations

from typing import Any

from domain.policies.redaction import sanitize_text


def safe_event_payload(payload: dict[str, object]) -> dict[str, object]:
    """将事件载荷递归脱敏后规范化为字典。

    Args:
        payload: 当前操作的结构化载荷。
    """
    safe = _safe_event_value(payload)
    if isinstance(safe, dict):
        return safe
    return {"value": safe}


def truncate(value: str, limit: int = 1000) -> str:
    """将过长文本截断到指定长度，避免事件和日志无限膨胀。

    Args:
        value: 待校验、归一化或转换的输入值。
        limit: 返回结果的最大数量。
    """
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def _safe_event_value(value: Any) -> Any:
    """递归清理事件值中的敏感文本和不可序列化对象。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    if isinstance(value, str):
        return sanitize_text(value)[:2000]
    if isinstance(value, dict):
        return {str(key): _safe_event_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_safe_event_value(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return sanitize_text(value)[:2000]


__all__ = ["safe_event_payload", "truncate"]
