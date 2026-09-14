from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.support.errors import AppError
from application.session_context.conversations import ConversationError
from application.session_context.resource_references import (
    _MAX_UPLOADED_FILE_BYTES,
    ResourceReferenceError,
    ResourceReferenceService,
    ResourceReferenceView,
)
from channels.desktop.local.schemas import (
    LocalArtifactResourceReferenceCreateRequest,
    LocalConversationActorRequest,
    LocalExternalRecordResourceReferenceCreateRequest,
    LocalResourceReferenceCreateRequest,
    LocalResourceReferenceListResponse,
    LocalResourceReferenceResponse,
)
from infrastructure.persistence.database import get_session

router = APIRouter()


def _resource_response(item: ResourceReferenceView) -> LocalResourceReferenceResponse:
    """Map a server-only Resource Reference view into a path-free API response.

    Args:
        item: 用于执行当前操作的 item 参数。
    """
    return LocalResourceReferenceResponse(
        reference_id=item.id,
        conversation_id=item.conversation_id,
        resource_kind=item.resource_kind,
        provider=item.provider,
        display_name=item.display_name,
        version=item.version,
        content_hash=item.content_hash,
        size_bytes=item.size_bytes,
        user_id=item.user_id,
        tenant_id=item.tenant_id,
        organization_id=item.organization_id,
        owner_type=item.owner_type,
        owner_id=item.owner_id,
        visibility=item.visibility,
        sensitivity=item.sensitivity,
        status=item.status,
        pinned=item.pinned,
        created_at=item.created_at,
        updated_at=item.updated_at,
        last_resolved_at=item.last_resolved_at,
    )


def _raise_resource_error(exc: Exception) -> None:
    """Convert internal Conversation/resource failures into bounded API errors.

    Args:
        exc: 用于执行当前操作的 exc 参数。
    """
    if isinstance(exc, ConversationError):
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    if isinstance(exc, ResourceReferenceError):
        raise AppError(exc.code, "Resource operation failed.", exc.status_code) from exc
    raise exc


@router.post(
    "/conversations/{conversation_id}/resources",
    response_model=LocalResourceReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def local_attach_conversation_resource(
    conversation_id: str,
    payload: LocalResourceReferenceCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalResourceReferenceResponse:
    """Attach one explicit local file to an active owned Conversation.

    Args:
        conversation_id: 目标会话 ID。
        payload: 当前操作的结构化载荷。
        session: 当前数据库异步会话。
    """
    try:
        item = await ResourceReferenceService(session).attach_local_file(
            conversation_id=conversation_id,
            user_id=payload.user_id,
            source_ref=payload.source_ref,
            sensitivity=payload.sensitivity,
            pinned=payload.pinned,
        )
    except (ConversationError, ResourceReferenceError) as exc:
        _raise_resource_error(exc)
        raise AssertionError("unreachable")
    return _resource_response(item)


@router.post(
    "/conversations/{conversation_id}/resources/import",
    response_model=LocalResourceReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def local_import_conversation_resource(
    conversation_id: str,
    payload: LocalResourceReferenceCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalResourceReferenceResponse:
    """Copy one selected desktop file into managed upload storage.

    Args:
        conversation_id: 目标会话 ID。
        payload: 当前操作的结构化载荷。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
    """
    try:
        item = await ResourceReferenceService(
            session,
            uploaded_resources_root=request.app.state.settings.resource_uploads_root,
            artifacts_root=request.app.state.settings.artifacts_root,
        ).import_local_file(
            conversation_id=conversation_id,
            user_id=payload.user_id,
            source_ref=payload.source_ref,
            sensitivity=payload.sensitivity,
            pinned=payload.pinned,
        )
    except (ConversationError, ResourceReferenceError) as exc:
        _raise_resource_error(exc)
        raise AssertionError("unreachable")
    return _resource_response(item)


@router.post(
    "/conversations/{conversation_id}/resources/upload",
    response_model=LocalResourceReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def local_upload_conversation_resource(
    conversation_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: Annotated[str, Form(min_length=1)],
    file: Annotated[UploadFile, File()],
    sensitivity: Annotated[str, Form()] = "normal",
    pinned: Annotated[bool, Form()] = False,
) -> LocalResourceReferenceResponse:
    """Store one bounded explicitly selected file under a managed input root.

    Args:
        conversation_id: 目标会话 ID。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
        user_id: 目标用户 ID。
        file: 用于执行当前操作的 file 参数。
        sensitivity: 用于执行当前操作的 sensitivity 参数。
        pinned: 用于执行当前操作的 pinned 参数。
    """
    content = await file.read(_MAX_UPLOADED_FILE_BYTES + 1)
    try:
        item = await ResourceReferenceService(
            session,
            uploaded_resources_root=request.app.state.settings.resource_uploads_root,
            artifacts_root=request.app.state.settings.artifacts_root,
        ).attach_uploaded_file(
            conversation_id=conversation_id,
            user_id=user_id,
            filename=file.filename or "",
            content=content,
            sensitivity=sensitivity,
            pinned=pinned,
        )
    except (ConversationError, ResourceReferenceError) as exc:
        _raise_resource_error(exc)
        raise AssertionError("unreachable")
    return _resource_response(item)


@router.post(
    "/conversations/{conversation_id}/resources/artifact",
    response_model=LocalResourceReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def local_attach_artifact_resource(
    conversation_id: str,
    payload: LocalArtifactResourceReferenceCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalResourceReferenceResponse:
    """Attach one authorized persistent Artifact without exposing storage details.

    Args:
        conversation_id: 目标会话 ID。
        payload: 当前操作的结构化载荷。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
    """
    try:
        item = await ResourceReferenceService(
            session,
            uploaded_resources_root=request.app.state.settings.resource_uploads_root,
            artifacts_root=request.app.state.settings.artifacts_root,
        ).attach_artifact(
            conversation_id=conversation_id,
            user_id=payload.user_id,
            artifact_id=payload.artifact_id,
            sensitivity=payload.sensitivity,
            pinned=payload.pinned,
        )
    except (ConversationError, ResourceReferenceError) as exc:
        _raise_resource_error(exc)
        raise AssertionError("unreachable")
    return _resource_response(item)


@router.post(
    "/conversations/{conversation_id}/resources/external-record",
    response_model=LocalResourceReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def local_attach_external_record_resource(
    conversation_id: str,
    payload: LocalExternalRecordResourceReferenceCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalResourceReferenceResponse:
    """Attach one opaque external identity without connecting a provider.

    Args:
        conversation_id: 目标会话 ID。
        payload: 当前操作的结构化载荷。
        session: 当前数据库异步会话。
    """
    try:
        item = await ResourceReferenceService(session).attach_external_record(
            conversation_id=conversation_id,
            user_id=payload.user_id,
            provider=payload.provider,
            external_resource_id=payload.external_resource_id,
            display_name=payload.display_name,
            version=payload.version,
            sensitivity=payload.sensitivity,
            pinned=payload.pinned,
        )
    except (ConversationError, ResourceReferenceError) as exc:
        _raise_resource_error(exc)
        raise AssertionError("unreachable")
    return _resource_response(item)


@router.get(
    "/conversations/{conversation_id}/space-context",
    response_model=LocalResourceReferenceListResponse,
)
async def local_list_space_context_candidates(
    conversation_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalResourceReferenceListResponse:
    """List safe same-owner context candidates from other Conversations in this Space.

    Args:
        conversation_id: 目标会话 ID。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    try:
        items = await ResourceReferenceService(session).list_space_context_candidates(
            conversation_id=conversation_id, user_id=user_id
        )
    except (ConversationError, ResourceReferenceError) as exc:
        _raise_resource_error(exc)
        raise AssertionError("unreachable")
    return LocalResourceReferenceListResponse(
        items=[_resource_response(item) for item in items]
    )


@router.post(
    "/conversations/{conversation_id}/space-context/{reference_id}",
    response_model=LocalResourceReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def local_attach_space_context_candidate(
    conversation_id: str,
    reference_id: str,
    payload: LocalConversationActorRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalResourceReferenceResponse:
    """Attach a selected same-owner, same-Space candidate via Resource Reference service.

    Args:
        conversation_id: 目标会话 ID。
        reference_id: 用于执行当前操作的 reference id 参数。
        payload: 当前操作的结构化载荷。
        session: 当前数据库异步会话。
    """
    try:
        item = await ResourceReferenceService(session).attach_space_context_candidate(
            conversation_id=conversation_id,
            reference_id=reference_id,
            user_id=payload.user_id,
        )
    except (ConversationError, ResourceReferenceError) as exc:
        _raise_resource_error(exc)
        raise AssertionError("unreachable")
    return _resource_response(item)


@router.get(
    "/conversations/{conversation_id}/resources",
    response_model=LocalResourceReferenceListResponse,
)
async def local_list_conversation_resources(
    conversation_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalResourceReferenceListResponse:
    """List attached Resource References for one active owned Conversation.

    Args:
        conversation_id: 目标会话 ID。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    try:
        items = await ResourceReferenceService(session).list_attached(
            conversation_id=conversation_id,
            user_id=user_id,
        )
    except (ConversationError, ResourceReferenceError) as exc:
        _raise_resource_error(exc)
        raise AssertionError("unreachable")
    return LocalResourceReferenceListResponse(
        items=[_resource_response(item) for item in items]
    )


@router.delete(
    "/conversations/{conversation_id}/resources/{reference_id}",
    response_model=LocalResourceReferenceResponse,
)
async def local_remove_conversation_resource(
    conversation_id: str,
    reference_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocalResourceReferenceResponse:
    """Soft-remove one Resource Reference without touching its source file.

    Args:
        conversation_id: 目标会话 ID。
        reference_id: 用于执行当前操作的 reference id 参数。
        user_id: 目标用户 ID。
        session: 当前数据库异步会话。
    """
    try:
        item = await ResourceReferenceService(session).remove(
            conversation_id=conversation_id,
            reference_id=reference_id,
            user_id=user_id,
        )
    except (ConversationError, ResourceReferenceError) as exc:
        _raise_resource_error(exc)
        raise AssertionError("unreachable")
    return _resource_response(item)


@router.get("/mentions", response_model=LocalResourceReferenceListResponse)
async def local_search_mentions(
    user_id: Annotated[str, Query(min_length=1)],
    conversation_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    query: Annotated[str, Query(max_length=200)] = "",
    kind: Annotated[
        Literal["local_file", "uploaded_file", "artifact", "external_record"] | None,
        Query(),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> LocalResourceReferenceListResponse:
    """Search attached resource display names within one authorized Conversation.

    Args:
        user_id: 目标用户 ID。
        conversation_id: 目标会话 ID。
        session: 当前数据库异步会话。
        query: 用于执行当前操作的 query 参数。
        kind: 用于执行当前操作的 kind 参数。
        limit: 返回结果的最大数量。
    """
    try:
        items = await ResourceReferenceService(session).search_mentions(
            conversation_id=conversation_id,
            user_id=user_id,
            query=query,
            resource_kind=kind,
            limit=limit,
        )
    except (ConversationError, ResourceReferenceError) as exc:
        _raise_resource_error(exc)
        raise AssertionError("unreachable")
    return LocalResourceReferenceListResponse(
        items=[_resource_response(item) for item in items]
    )


__all__ = [
    "local_attach_conversation_resource",
    "local_import_conversation_resource",
    "local_attach_artifact_resource",
    "local_attach_external_record_resource",
    "local_upload_conversation_resource",
    "local_list_conversation_resources",
    "local_list_space_context_candidates",
    "local_attach_space_context_candidate",
    "local_remove_conversation_resource",
    "local_search_mentions",
    "router",
]
