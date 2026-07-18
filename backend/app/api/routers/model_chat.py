from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from model_gateway.chat_service import handle_model_chat
from app.api.schemas import (
    ModelChatRequest,
    ModelChatResponse,
)

router = APIRouter()


@router.post("/internal/models/chat", response_model=ModelChatResponse)
async def chat_with_model(
    payload: ModelChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelChatResponse:
    return await handle_model_chat(
        payload=payload,
        session=session,
        settings=request.app.state.settings,
    )
