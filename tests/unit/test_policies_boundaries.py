from __future__ import annotations

from domain.models import ApprovalType, TaskStatus
from domain.policies.approval_requests import normalize_approval_requests
from domain.policies.task_status import DISPATCHABLE_TASK_STATUSES, VALID_TRANSITIONS
from domain.policies.tool_approval import (
    external_approval_binding,
    external_audit_arguments,
)


def test_task_status_policy_keeps_transition_rules_outside_application() -> None:
    assert TaskStatus.RUNNING in VALID_TRANSITIONS[TaskStatus.PENDING]
    assert TaskStatus.SUCCESS not in VALID_TRANSITIONS[TaskStatus.PENDING]
    assert TaskStatus.WAITING_APPROVAL.value in DISPATCHABLE_TASK_STATUSES


def test_normalize_approval_requests_deduplicates_and_redacts() -> None:
    requests = [
        {
            "approval_type": ApprovalType.TOOL.value,
            "subject": "email.send",
            "summary": "Bearer secret-token",
            "tool_name": "email.send",
        },
        {
            "approval_type": ApprovalType.TOOL.value,
            "subject": "email.send",
            "summary": "Bearer secret-token",
            "tool_name": "email.send",
        },
        {"approval_type": "unknown", "subject": "ignored"},
    ]

    normalized = normalize_approval_requests(requests)

    assert normalized == (("tool", "email.send", "[REDACTED]", "email.send"),)


def test_external_tool_approval_binding_uses_fingerprint_not_raw_arguments() -> None:
    binding = external_approval_binding(
        "email.send",
        {"to": ["user@example.com"], "subject": "Hello", "body": "raw body"},
    )
    audit = external_audit_arguments("email.send", {"body": "raw body"})

    assert binding.subject.startswith("email.send:")
    assert len(binding.fingerprint) == 64
    assert (
        audit["argument_fingerprint"]
        == external_approval_binding("email.send", {"body": "raw body"}).fingerprint
    )
    assert "raw body" not in str(audit)
