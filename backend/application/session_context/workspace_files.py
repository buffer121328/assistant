from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.session_context.spaces import SpaceService
from application.session_context.resource_references import ResourceReferenceError, ResourceReferenceService
from domain.models import ArtifactRecord, Conversation, ConversationResourceReference
from tools.builtin.artifacts import ArtifactPathError, ArtifactStore

_MAX_WORKSPACE_FILE_ITEMS = 200


@dataclass(frozen=True)
class WorkspaceFileView:
    """One safe file-list item for a user's current private Workspace."""

    id: str
    source_kind: str
    display_name: str
    media_type: str | None
    size_bytes: int
    conversation_id: str
    conversation_title: str
    created_at: datetime
    updated_at: datetime


class WorkspaceFileService:
    """Build owner-scoped file collections without exposing managed storage."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        uploaded_resources_root: Path = Path("var/resource-inputs"),
        artifacts_root: Path = Path("var/artifacts"),
    ) -> None:
        self.session = session
        self.uploaded_resources_root = uploaded_resources_root
        self.artifact_store = ArtifactStore(artifacts_root)

    async def list_owned_workspace(
        self,
        *,
        workspace_id: str,
        user_id: str,
        limit: int = _MAX_WORKSPACE_FILE_ITEMS,
    ) -> tuple[WorkspaceFileView, ...]:
        """List eligible uploads and generated files for one private Workspace."""
        await SpaceService(self.session).require_owned_active(
            space_id=workspace_id,
            user_id=user_id,
        )
        bounded_limit = max(1, min(limit, _MAX_WORKSPACE_FILE_ITEMS))
        uploads = await self.session.execute(
            select(ConversationResourceReference, Conversation)
            .join(
                Conversation,
                ConversationResourceReference.conversation_id == Conversation.id,
            )
            .where(
                ConversationResourceReference.user_id == user_id,
                ConversationResourceReference.owner_type == "user",
                ConversationResourceReference.owner_id == user_id,
                ConversationResourceReference.resource_kind == "uploaded_file",
                ConversationResourceReference.provider == "managed_upload",
                ConversationResourceReference.status == "attached",
                Conversation.user_id == user_id,
                Conversation.owner_type == "user",
                Conversation.owner_id == user_id,
                Conversation.space_id == workspace_id,
            )
            .order_by(
                ConversationResourceReference.created_at.desc(),
                ConversationResourceReference.id.desc(),
            )
            .limit(bounded_limit)
        )
        artifacts = await self.session.execute(
            select(ArtifactRecord, Conversation)
            .join(Conversation, ArtifactRecord.conversation_id == Conversation.id)
            .where(
                ArtifactRecord.owner_type == "user",
                ArtifactRecord.owner_id == user_id,
                ArtifactRecord.created_by == user_id,
                ArtifactRecord.visibility == "private",
                ArtifactRecord.lifecycle_state.in_(("registered", "archived")),
                ArtifactRecord.revoked_at.is_(None),
                Conversation.user_id == user_id,
                Conversation.owner_type == "user",
                Conversation.owner_id == user_id,
                Conversation.space_id == workspace_id,
            )
            .order_by(ArtifactRecord.created_at.desc(), ArtifactRecord.id.desc())
            .limit(bounded_limit)
        )
        items: list[WorkspaceFileView] = []
        resource_service = ResourceReferenceService(
            self.session,
            uploaded_resources_root=self.uploaded_resources_root,
        )
        for item, conversation in uploads.all():
            try:
                await resource_service.resolve_uploaded_download(
                    reference_id=item.id,
                    user_id=user_id,
                )
            except ResourceReferenceError:
                continue
            items.append(
                WorkspaceFileView(
                    id=item.id,
                    source_kind="uploaded",
                    display_name=item.display_name,
                    media_type=None,
                    size_bytes=item.size_bytes,
                    conversation_id=conversation.id,
                    conversation_title=conversation.title,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )
        for item, conversation in artifacts.all():
            try:
                inspection = self.artifact_store.inspect(
                    task_id=item.task_id,
                    reference=item.storage_reference,
                )
            except (ArtifactPathError, OSError):
                continue
            if (
                inspection.content_hash != item.content_hash
                or inspection.size_bytes != item.size_bytes
            ):
                continue
            items.append(
                WorkspaceFileView(
                    id=item.id,
                    source_kind="generated",
                    display_name=item.display_filename,
                    media_type=item.media_type,
                    size_bytes=item.size_bytes,
                    conversation_id=conversation.id,
                    conversation_title=conversation.title,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )
        items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return tuple(items[:bounded_limit])
