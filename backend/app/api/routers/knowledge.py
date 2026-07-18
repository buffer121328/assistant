from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge import KnowledgeError, KnowledgeService, MAX_IMPORT_BYTES

from infrastructure.database import get_session
from app.support.errors import AppError
from app.api.schemas import (
    KnowledgeDocumentListResponse,
    KnowledgeDocumentResponse,
    KnowledgeImportResponse,
    KnowledgeSearchItem,
    KnowledgeSearchResponse,
)

router = APIRouter()


def raise_knowledge_error(exc: KnowledgeError) -> None:
    raise AppError(
        code=exc.code,
        message="Knowledge operation failed.",
        status_code=404 if exc.code == "knowledge_user_not_found" else 400,
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
    items = await KnowledgeService(
        session, import_root=request.app.state.settings.knowledge_root
    ).list_documents(user_id=user_id)
    return KnowledgeDocumentListResponse(
        items=[KnowledgeDocumentResponse(**item.__dict__) for item in items]
    )


@router.get("/api/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    query: Annotated[str, Query(min_length=1, max_length=200)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> KnowledgeSearchResponse:
    try:
        results = await KnowledgeService(
            session, import_root=request.app.state.settings.knowledge_root
        ).search(user_id=user_id, query=query, limit=limit)
    except KnowledgeError as exc:
        raise_knowledge_error(exc)
    return KnowledgeSearchResponse(
        items=[KnowledgeSearchItem(**item.__dict__) for item in results]
    )
