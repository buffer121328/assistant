from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.support.errors import AppError
from application.lifecycle_hooks import build_builtin_lifecycle_registry
from application.artifact_lifecycle import (
    ArtifactLifecycleError,
    ArtifactLifecycleService,
    ArtifactView,
)
from channels.desktop.local.schemas import (
    LocalArtifactActionRequest,
    LocalArtifactListResponse,
    LocalArtifactResponse,
)
from infrastructure.persistence.database import get_session
from tools.builtin.artifacts import ArtifactStore

router = APIRouter()


def _artifact_response(item: ArtifactView) -> LocalArtifactResponse:
    """Map an internal path-free Artifact view to its local API schema.

    Args:
        item: 用于执行当前操作的 item 参数。
    """
    return LocalArtifactResponse(
        artifact_id=item.id,
        tenant_id=item.tenant_id,
        organization_id=item.organization_id,
        owner_type=item.owner_type,
        owner_id=item.owner_id,
        conversation_id=item.conversation_id,
        task_id=item.task_id,
        run_id=item.run_id,
        filename=item.filename,
        media_type=item.media_type,
        size_bytes=item.size_bytes,
        content_hash=item.content_hash,
        version=item.version,
        generation_method=item.generation_method,
        source_reference_versions=item.source_reference_versions,
        visibility=item.visibility,
        sensitivity=item.sensitivity,
        lifecycle_state=item.lifecycle_state,
        publication_state=item.publication_state,
        retention_state=item.retention_state,
        created_at=item.created_at,
        updated_at=item.updated_at,
        archived_at=item.archived_at,
        revoked_at=item.revoked_at,
    )


def _service(
    session: AsyncSession,
    request: Request,
) -> ArtifactLifecycleService:
    """Build the lifecycle service with the configured backend-owned store root.

    Args:
        session: 当前数据库异步会话。
        request: 当前操作的结构化请求。
    """
    return ArtifactLifecycleService(
        session,
        store=ArtifactStore(request.app.state.settings.artifacts_root),
        lifecycle_registry=build_builtin_lifecycle_registry(
            request.app.state.db_sessionmaker,
            session=session,
        ),
    )


def _raise_artifact_error(exc: ArtifactLifecycleError) -> None:
    """Convert internal Artifact failures into one bounded local API error.

    Args:
        exc: 用于执行当前操作的 exc 参数。
    """
    raise AppError(exc.code, "Artifact operation failed.", exc.status_code) from exc


@router.get(
    "/conversations/{conversation_id}/artifacts",
    response_model=LocalArtifactListResponse,
)
async def local_list_conversation_artifacts(
    conversation_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalArtifactListResponse:
    """List governed Artifacts for one exactly owned local Conversation.

    Args:
        conversation_id: 目标会话 ID。
        user_id: 目标用户 ID。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
    """
    try:
        items = await _service(session, request).list_conversation(
            conversation_id=conversation_id,
            actor_user_id=user_id,
        )
    except ArtifactLifecycleError as exc:
        _raise_artifact_error(exc)
        raise AssertionError("unreachable")
    return LocalArtifactListResponse(items=[_artifact_response(item) for item in items])


@router.get("/artifacts/{artifact_id}/download", response_class=FileResponse)
async def local_download_artifact(
    artifact_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    """Return only owner-authorized bytes after managed integrity verification.

    Args:
        artifact_id: 目标产物 ID。
        user_id: 目标用户 ID。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
    """
    try:
        download = await _service(session, request).resolve_download(
            artifact_id=artifact_id,
            actor_user_id=user_id,
        )
    except ArtifactLifecycleError as exc:
        _raise_artifact_error(exc)
        raise AssertionError("unreachable")
    return FileResponse(
        download.path,
        media_type=download.media_type,
        filename=download.filename,
    )


@router.post(
    "/artifacts/{artifact_id}/archive",
    response_model=LocalArtifactResponse,
)
async def local_archive_artifact(
    artifact_id: str,
    payload: LocalArtifactActionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalArtifactResponse:
    """Archive an owner-scoped Artifact without deleting its retained bytes.

    Args:
        artifact_id: 目标产物 ID。
        payload: 当前操作的结构化载荷。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
    """
    try:
        item = await _service(session, request).archive(
            artifact_id=artifact_id,
            actor_user_id=payload.user_id,
        )
    except ArtifactLifecycleError as exc:
        _raise_artifact_error(exc)
        raise AssertionError("unreachable")
    return _artifact_response(item)


@router.post(
    "/artifacts/{artifact_id}/revoke",
    response_model=LocalArtifactResponse,
)
async def local_revoke_artifact(
    artifact_id: str,
    payload: LocalArtifactActionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalArtifactResponse:
    """Revoke normal access while retaining Artifact metadata and audit facts.

    Args:
        artifact_id: 目标产物 ID。
        payload: 当前操作的结构化载荷。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
    """
    try:
        item = await _service(session, request).revoke(
            artifact_id=artifact_id,
            actor_user_id=payload.user_id,
        )
    except ArtifactLifecycleError as exc:
        _raise_artifact_error(exc)
        raise AssertionError("unreachable")
    return _artifact_response(item)


__all__ = [
    "local_archive_artifact",
    "local_download_artifact",
    "local_list_conversation_artifacts",
    "local_revoke_artifact",
    "router",
]
