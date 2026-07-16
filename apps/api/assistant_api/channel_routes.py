from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .langbot import handle_langbot_webhook
from .model_gateway import handle_model_chat
from .schemas import (
    LangBotWebhookRequest,
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


@router.post("/api/webhooks/langbot")
async def receive_langbot_webhook(
    payload: LangBotWebhookRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    return await handle_langbot_webhook(
        payload=payload,
        headers=request.headers,
        session=session,
        settings=request.app.state.settings,
    )
