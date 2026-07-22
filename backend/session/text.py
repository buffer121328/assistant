from __future__ import annotations

from collections.abc import Sequence

from domain.policies.redaction import sanitize_text


def safe_text(value: str) -> str:
    """Sanitize and normalize conversation memory text."""
    return " ".join(sanitize_text(value).strip().split())[:20_000]


def safe_items(values: Sequence[str]) -> tuple[str, ...]:
    """Sanitize a sequence and discard empty items."""
    return tuple(item for value in values if (item := safe_text(value)))


def bounded_item(value: str, limit: int) -> str:
    """Sanitize one item and truncate it to a display-friendly limit."""
    safe = safe_text(value)
    if len(safe) <= limit:
        return safe
    return f"{safe[:limit]}..."


def merge_limited(
    existing: Sequence[str], additions: Sequence[str], *, limit: int
) -> tuple[str, ...]:
    """Merge unique sanitized items and keep only the newest limited tail."""
    result: list[str] = []
    seen: set[str] = set()
    for value in tuple(existing) + tuple(additions):
        item = safe_text(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result[-limit:])
