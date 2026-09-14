from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from application.session_context.context_service import ConversationMemoryService
    from application.session_context.context_snapshots import (
        TaskContextSnapshotNotFoundError,
        TaskContextSnapshotService,
        TaskContextSnapshotView,
    )
    from application.session_context.conversations import (
        ConversationError,
        ConversationService,
        ConversationTokenStats,
    )
    from application.session_context.execution_workspaces import (
        ExecutionWorkspaceResult,
        ExecutionWorkspaceService,
        PreparedWorkspaceResource,
        WorkspaceCleanupDecision,
    )
    from application.session_context.resource_references import (
        ResolvedResourceReference,
        ResourceReferenceError,
        ResourceReferenceService,
        ResourceReferenceView,
    )
    from application.session_context.summary import (
        ConversationSummarizer,
        HeuristicConversationSummarizer,
        SummaryDraft,
    )


_EXPORTS: dict[str, tuple[str, str]] = {
    "ConversationError": (
        "application.session_context.conversations",
        "ConversationError",
    ),
    "ConversationMemoryService": (
        "application.session_context.context_service",
        "ConversationMemoryService",
    ),
    "ConversationService": (
        "application.session_context.conversations",
        "ConversationService",
    ),
    "ConversationSummarizer": (
        "application.session_context.summary",
        "ConversationSummarizer",
    ),
    "ConversationTokenStats": (
        "application.session_context.conversations",
        "ConversationTokenStats",
    ),
    "ExecutionWorkspaceResult": (
        "application.session_context.execution_workspaces",
        "ExecutionWorkspaceResult",
    ),
    "ExecutionWorkspaceService": (
        "application.session_context.execution_workspaces",
        "ExecutionWorkspaceService",
    ),
    "PreparedWorkspaceResource": (
        "application.session_context.execution_workspaces",
        "PreparedWorkspaceResource",
    ),
    "WorkspaceCleanupDecision": (
        "application.session_context.execution_workspaces",
        "WorkspaceCleanupDecision",
    ),
    "WorkspaceFileService": (
        "application.session_context.workspace_files",
        "WorkspaceFileService",
    ),
    "WorkspaceFileView": (
        "application.session_context.workspace_files",
        "WorkspaceFileView",
    ),
    "HeuristicConversationSummarizer": (
        "application.session_context.summary",
        "HeuristicConversationSummarizer",
    ),
    "SummaryDraft": ("application.session_context.summary", "SummaryDraft"),
    "ResolvedResourceReference": (
        "application.session_context.resource_references",
        "ResolvedResourceReference",
    ),
    "ResourceReferenceError": (
        "application.session_context.resource_references",
        "ResourceReferenceError",
    ),
    "ResourceReferenceService": (
        "application.session_context.resource_references",
        "ResourceReferenceService",
    ),
    "ResourceReferenceView": (
        "application.session_context.resource_references",
        "ResourceReferenceView",
    ),
    "TaskContextSnapshotNotFoundError": (
        "application.session_context.context_snapshots",
        "TaskContextSnapshotNotFoundError",
    ),
    "TaskContextSnapshotService": (
        "application.session_context.context_snapshots",
        "TaskContextSnapshotService",
    ),
    "TaskContextSnapshotView": (
        "application.session_context.context_snapshots",
        "TaskContextSnapshotView",
    ),
}

__all__ = [
    "ConversationError",
    "ConversationMemoryService",
    "ConversationService",
    "ConversationSummarizer",
    "ConversationTokenStats",
    "ExecutionWorkspaceResult",
    "ExecutionWorkspaceService",
    "PreparedWorkspaceResource",
    "WorkspaceCleanupDecision",
    "WorkspaceFileService",
    "WorkspaceFileView",
    "HeuristicConversationSummarizer",
    "SummaryDraft",
    "ResolvedResourceReference",
    "ResourceReferenceError",
    "ResourceReferenceService",
    "ResourceReferenceView",
    "TaskContextSnapshotNotFoundError",
    "TaskContextSnapshotService",
    "TaskContextSnapshotView",
]


def __getattr__(name: str) -> Any:
    """Resolve compatibility exports only when callers request them."""
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy compatibility names to reflection-based callers."""
    return sorted({*globals(), *__all__})
