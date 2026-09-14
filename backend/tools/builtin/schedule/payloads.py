from __future__ import annotations

from datetime import datetime

from domain.policies.redaction import sanitize_text


def _safe_payload(payload: dict[str, object]) -> dict[str, object]:
    """执行 处理 safe payload 的内部辅助逻辑。

    Args:
        payload: payload 参数。
    """
    allowed = {"task_type", "input_text", "workflow_key", "model_class"}
    safe = {
        key: sanitize_text(value) if isinstance(value, str) else value
        for key, value in payload.items()
        if key in allowed
    }
    if not str(safe.get("input_text") or "").strip():
        raise ValueError("payload.input_text is required")
    return safe


def _parse_datetime(value: object) -> datetime | None:
    """执行 解析 datetime 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _optional_str(value: object) -> str | None:
    """执行 处理 optional str 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if value is None or str(value).strip() == "":
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    """执行 处理 optional int 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("integer value is required")
