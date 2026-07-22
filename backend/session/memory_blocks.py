from __future__ import annotations

from session.conversations import ConversationError
from memory.working_set import estimate_tokens

BLOCK_TYPES = {
    "human_profile",
    "communication_style",
    "stable_constraints",
    "current_priorities",
    "project_context",
}

BLOCK_ORDER = {
    "stable_constraints": 0,
    "human_profile": 1,
    "communication_style": 2,
    "current_priorities": 3,
    "project_context": 4,
}

VALID_SCOPE_KINDS = {
    "user/global",
    "user/project",
    "user/conversation",
    "system/read_only",
}


def validate_block_identity(
    *, block_type: str, scope_kind: str, scope_id: str | None
) -> None:
    """Validate memory block type and scope identity."""
    if block_type not in BLOCK_TYPES:
        raise ConversationError("memory_block_type_invalid")
    if scope_kind not in VALID_SCOPE_KINDS:
        raise ConversationError("memory_block_scope_invalid")
    if scope_kind != "user/global" and not scope_id:
        raise ConversationError("memory_block_scope_id_required")


def validate_block_content(
    content: str, *, character_limit: int, token_limit: int
) -> int:
    """Validate memory block content bounds and return the estimated token count."""
    tokens = estimate_tokens(content)
    if not content or len(content) > character_limit or tokens > token_limit:
        raise ConversationError("memory_block_limit_exceeded")
    return tokens
