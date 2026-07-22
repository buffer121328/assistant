from __future__ import annotations

from collections.abc import Iterable, Mapping

from domain.policies.redaction import sanitize_text
from domain.models import ApprovalType


def normalize_approval_requests(
    requests: Iterable[object],
) -> tuple[tuple[str, str, str, str | None], ...]:
    """Normalize free-form approval requests into safe, deduplicated tuples."""
    normalized: list[tuple[str, str, str, str | None]] = []
    for request in requests:
        if isinstance(request, Mapping):
            approval_type = request.get("approval_type")
            subject = request.get("subject")
            summary = request.get("summary")
            tool_name = request.get("tool_name")
        else:
            approval_type = getattr(request, "approval_type", None)
            subject = getattr(request, "subject", None)
            summary = getattr(request, "summary", None)
            tool_name = getattr(request, "tool_name", None)
        if not isinstance(approval_type, str) or approval_type not in {
            item.value for item in ApprovalType
        }:
            continue
        if not isinstance(subject, str) or not subject.strip():
            continue
        safe_subject = sanitize_text(subject).strip()[:128]
        safe_summary = sanitize_text(summary or "需要人工审批。").strip()[:1000]
        safe_tool_name = (
            sanitize_text(tool_name).strip()[:128]
            if isinstance(tool_name, str) and tool_name.strip()
            else None
        )
        item = (approval_type, safe_subject, safe_summary, safe_tool_name)
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)
