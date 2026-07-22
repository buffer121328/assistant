from __future__ import annotations

from typing import Any

from domain.policies.redaction import sanitize_text


def safe_event_payload(payload: dict[str, object]) -> dict[str, object]:
    safe = _safe_event_value(payload)
    if isinstance(safe, dict):
        return safe
    return {"value": safe}


def truncate(value: str, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def _safe_event_value(value: Any) -> Any:
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
