from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from rag import KnowledgeError, KnowledgeService, MAX_IMPORT_BYTES

from infrastructure.persistence.database import get_session
from app.support.errors import AppError
from app.api.schemas import (
    KnowledgeDeleteResponse,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentResponse,
    KnowledgeImportResponse,
    KnowledgeSearchItem,
    KnowledgeSearchResponse,
)

router = APIRouter()


def raise_knowledge_error(exc: KnowledgeError) -> None:
    """处理 raise knowledge error。

    Args:
        exc: exc 参数。
    """
    raise AppError(
        code=exc.code,
        message="Knowledge operation failed.",
        status_code=404
        if exc.code in {"knowledge_user_not_found", "knowledge_document_not_found"}
        else 400,
    ) from exc


@router.post(
    "/api/knowledge/import",
    response_model=KnowledgeImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_knowledge(
    request: Request,
    user_id: Annotated[str, Form(min_length=1)],
    document: Annotated[UploadFile, File()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeImportResponse:
    """处理 import knowledge。

    Args:
        request: request 参数。
        user_id: user_id 参数。
        document: document 参数。
        session: session 参数。
    """
    content = await document.read(MAX_IMPORT_BYTES + 1)
    await document.close()
    try:
        result = await KnowledgeService(
            session, import_root=request.app.state.settings.knowledge_root
        ).store_upload(
            user_id=user_id,
            filename=document.filename or "",
            content=content,
        )
    except KnowledgeError as exc:
        raise_knowledge_error(exc)
    return KnowledgeImportResponse(**result.__dict__)


@router.get("/api/knowledge/documents", response_model=KnowledgeDocumentListResponse)
async def list_knowledge_documents(
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeDocumentListResponse:
    """列出 knowledge documents。

    Args:
        request: request 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
    items = await KnowledgeService(
        session, import_root=request.app.state.settings.knowledge_root
    ).list_documents(user_id=user_id)
    return KnowledgeDocumentListResponse(
        items=[KnowledgeDocumentResponse(**item.__dict__) for item in items]
    )


@router.delete(
    "/api/knowledge/documents/{document_id}",
    response_model=KnowledgeDeleteResponse,
)
async def delete_knowledge_document(
    document_id: str,
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeDeleteResponse:
    """删除 knowledge document。

    Args:
        document_id: document_id 参数。
        request: request 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
    try:
        result = await KnowledgeService(
            session, import_root=request.app.state.settings.knowledge_root
        ).delete_document(user_id=user_id, document_id=document_id)
    except KnowledgeError as exc:
        raise_knowledge_error(exc)
    return KnowledgeDeleteResponse(**result.__dict__)


@router.get("/api/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    query: Annotated[str, Query(min_length=1, max_length=200)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> KnowledgeSearchResponse:
    """搜索 knowledge。

    Args:
        request: request 参数。
        user_id: user_id 参数。
        query: query 参数。
        session: session 参数。
        limit: limit 参数。
    """
    try:
        results = await KnowledgeService(
            session, import_root=request.app.state.settings.knowledge_root
        ).search(user_id=user_id, query=query, limit=limit)
    except KnowledgeError as exc:
        raise_knowledge_error(exc)
    return KnowledgeSearchResponse(
        items=[KnowledgeSearchItem(**item.__dict__) for item in results],
        answerable=bool(results),
    )
