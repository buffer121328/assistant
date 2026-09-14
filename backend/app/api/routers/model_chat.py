from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ModelChatRequest,
    ModelChatResponse,
    ModelGatewayUsage,
)
from app.support.errors import AppError
from domain.policies.redaction import sanitize_text
from infrastructure.persistence.database import get_session
from infrastructure.settings.config import Settings
from model_gateway import GatewayMessage, GatewayRequest, GatewayResult, ModelGatewayError
from model_gateway.chat_service import handle_model_chat

router = APIRouter()


@router.post("/internal/models/chat", response_model=ModelChatResponse)
async def chat_with_model(
    payload: ModelChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelChatResponse:
    """Handle an internal model chat request through the model gateway.

    Args:
        payload: 当前操作的结构化载荷。
        request: 当前操作的结构化请求。
        session: 当前数据库异步会话。
    """
    settings: Settings = request.app.state.settings
    gateway_request = GatewayRequest(
        user_id=payload.user_id,
        task_id=payload.task_id,
        task_type=payload.task_type,
        model_class=payload.model_class,
        messages=tuple(
            GatewayMessage(role=message.role, content=message.content)
            for message in payload.messages
        ),
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
    )
    try:
        result = await handle_model_chat(
            request=gateway_request,
            session=session,
            settings=settings,
        )
    except ModelGatewayError as exc:
        raise _app_error(exc, settings) from exc
    return _response(result)


def _response(result: GatewayResult) -> ModelChatResponse:
    """Convert a gateway result into an API response DTO.

    Args:
        result: 需要处理的执行结果。
    """
    return ModelChatResponse(
        provider=result.provider,
        model=result.model,
        content=result.content,
        usage=ModelGatewayUsage(
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        ),
        latency_ms=result.latency_ms,
        status="succeeded",
    )


def _app_error(error: ModelGatewayError, settings: Settings) -> AppError:
    """Convert gateway errors into API-layer errors without leaking secrets.

    Args:
        error: 用于执行当前操作的 error 参数。
        settings: 用于执行当前操作的 settings 参数。
    """
    return AppError(
        code=error.code,
        message=sanitize_text(
            error.message,
            extra_sensitive_values=(
                settings.deepseek_api_key,
                settings.qwen_api_key,
                settings.qwen_base_url,
            ),
        ),
        status_code=error.status_code,
    )
