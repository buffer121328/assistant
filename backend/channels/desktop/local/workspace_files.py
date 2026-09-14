from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SpaceListResponse, SpaceResponse
from app.support.errors import AppError
from application.session_context.resource_references import (
    ResourceReferenceError,
    ResourceReferenceService,
)
from application.session_context.spaces import SpaceError, SpaceService
from application.session_context.workspace_files import (
    WorkspaceFileService,
    WorkspaceFileView,
)
from channels.desktop.local.schemas import (
    LocalWorkspaceFileListResponse,
    LocalWorkspaceFileResponse,
)
from infrastructure.persistence.database import get_session

router = APIRouter()


def _workspace_response(item: WorkspaceFileView) -> LocalWorkspaceFileResponse:
    """Serialize only safe Workspace file metadata for the desktop renderer."""
    return LocalWorkspaceFileResponse(
        file_id=item.id,
        source_kind=cast(Literal["uploaded", "generated"], item.source_kind),
        display_name=item.display_name,
        media_type=item.media_type,
        size_bytes=item.size_bytes,
        conversation_id=item.conversation_id,
        conversation_title=item.conversation_title,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get("/workspaces", response_model=SpaceListResponse)
async def local_list_owned_workspaces(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpaceListResponse:
    """List only the current user's active personal Workspaces."""
    try:
        spaces = await SpaceService(session).list_owned_active(user_id)
    except SpaceError as exc:
        raise AppError(exc.code, "Workspace operation failed.", exc.status_code) from exc
    return SpaceListResponse(
        items=[
            SpaceResponse(
                space_id=item.id,
                tenant_id=item.tenant_id,
                organization_id=item.organization_id,
                name=item.name,
                description=item.description,
                status=cast(Literal["active", "archived"], item.status),
                created_by=item.created_by,
                role="owner",
                archived_at=item.archived_at,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in spaces
        ]
    )


@router.get(
    "/workspaces/{workspace_id}/files",
    response_model=LocalWorkspaceFileListResponse,
)
async def local_list_workspace_files(
    workspace_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalWorkspaceFileListResponse:
    """List uploaded and generated files from the caller's private Workspace."""
    try:
        items = await WorkspaceFileService(
            session,
            uploaded_resources_root=request.app.state.settings.resource_uploads_root,
            artifacts_root=request.app.state.settings.artifacts_root,
        ).list_owned_workspace(
            workspace_id=workspace_id,
            user_id=user_id,
        )
    except SpaceError as exc:
        raise AppError(exc.code, "Workspace operation failed.", exc.status_code) from exc
    return LocalWorkspaceFileListResponse(items=[_workspace_response(item) for item in items])


@router.get("/workspace-files/{reference_id}/download", response_class=FileResponse)
async def local_download_uploaded_workspace_file(
    reference_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    """Return one active uploaded file after owner and integrity re-authorization."""
    try:
        download = await ResourceReferenceService(
            session,
            uploaded_resources_root=request.app.state.settings.resource_uploads_root,
            artifacts_root=request.app.state.settings.artifacts_root,
        ).resolve_uploaded_download(reference_id=reference_id, user_id=user_id)
    except ResourceReferenceError as exc:
        raise AppError(exc.code, "File operation failed.", exc.status_code) from exc
    return FileResponse(
        download.path,
        media_type=download.media_type,
        filename=download.filename,
    )
