from __future__ import annotations

import json

from domain.models import TaskEvent, ToolLog
from domain.policies.redaction import sanitize_text

from channels.desktop.local.schemas import LocalEventResponse


def local_event_response(event: TaskEvent) -> LocalEventResponse:
    """Convert a task event row into a sanitized desktop event payload."""
    return LocalEventResponse(
        event_id=event.id,
        task_id=event.task_id,
        type=event.event_type,
        created_at=event.created_at.isoformat(),
        sequence=event.sequence,
        payload=safe_payload(event.payload_json),
    )


def local_tool_log_response(log: ToolLog, *, sequence: int) -> LocalEventResponse:
    """Expose tool logs through the same local event envelope."""
    return LocalEventResponse(
        event_id=f"tool-log-{log.id}",
        task_id=log.task_id or "",
        type="task.log.appended",
        created_at=log.created_at.isoformat(),
        sequence=sequence,
        payload={
            "tool_name": sanitize_text(log.tool_name),
            "status": sanitize_text(log.status),
            "input": safe_payload_value(log.input_text),
            "output": safe_payload_value(log.output_text),
            "error": safe_payload_value(log.error_message),
        },
    )


def safe_payload(payload_json: str) -> dict[str, object]:
    """Load and sanitize a JSON payload for local delivery."""
    loaded = json.loads(payload_json)
    if isinstance(loaded, dict):
        return {
            str(key): safe_payload_value(value)
            for key, value in loaded.items()
            if not is_sensitive_key(str(key))
        }
    return {"value": safe_payload_value(loaded)}


def safe_payload_value(value: object) -> object:
    """Recursively redact sensitive values in local event/log payloads."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {
            str(key): safe_payload_value(item)
            for key, item in value.items()
            if not is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [safe_payload_value(item) for item in value]
    if isinstance(value, tuple):
        return [safe_payload_value(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return sanitize_text(value)


def is_sensitive_key(key: str) -> bool:
    """Return whether a payload key should be removed from local responses."""
    normalized = key.casefold()
    return any(
        marker in normalized
        for marker in (
            "authorization",
            "cookie",
            "api_key",
            "apikey",
            "token",
            "secret",
        )
    )
