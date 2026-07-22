from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import LangBotWebhookRequest
from channels.langbot.service import handle_langbot_webhook
from infrastructure.persistence.database import get_session

router = APIRouter()


@router.post("/api/webhooks/langbot")
async def receive_langbot_webhook(
    payload: LangBotWebhookRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """处理 receive langbot webhook。

    Args:
        payload: payload 参数。
        request: request 参数。
        session: session 参数。
    """
    return await handle_langbot_webhook(
        payload=payload,
        headers=request.headers,
        session=session,
        settings=request.app.state.settings,
    )
