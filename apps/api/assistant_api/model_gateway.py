from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from packages.model_gateway import (
    DeepSeekAdapter,
    DeepSeekConfig,
    GatewayMessage,
    GatewayRequest,
    GatewayResult,
    ModelGatewayError,
    build_error_summary,
    build_request_summary,
    build_response_summary,
    route_model,
    sanitize_text,
)

from .config import Settings
from .errors import AppError
from .repositories import ModelLogCreate, ModelLogRepository
from .schemas import ModelChatRequest, ModelChatResponse, ModelGatewayUsage


async def handle_model_chat(
    *,
    payload: ModelChatRequest,
    session: AsyncSession,
    settings: Settings,
) -> ModelChatResponse:
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
    sensitive_values = _sensitive_values(settings)

    try:
        resolved_model_class = route_model(
            gateway_request.task_type,
            gateway_request.model_class,
        )
    except ModelGatewayError as exc:
        raise _app_error(exc, sensitive_values) from exc

    request_summary = build_request_summary(
        gateway_request,
        resolved_model_class=resolved_model_class,
        extra_sensitive_values=sensitive_values,
    )
    adapter = DeepSeekAdapter(_deepseek_config(settings))
    repository = ModelLogRepository(session)

    try:
        result = await adapter.chat(gateway_request, resolved_model_class)
    except ModelGatewayError as exc:
        await repository.create_model_log(
            ModelLogCreate(
                task_id=gateway_request.task_id,
                model_class=resolved_model_class,
                request_text=request_summary,
                response_text=None,
                error_message=build_error_summary(
                    exc,
                    extra_sensitive_values=sensitive_values,
                ),
            )
        )
        await session.commit()
        raise _app_error(exc, sensitive_values) from exc

    await repository.create_model_log(
        ModelLogCreate(
            task_id=gateway_request.task_id,
            model_class=resolved_model_class,
            request_text=request_summary,
            response_text=build_response_summary(
                result,
                extra_sensitive_values=sensitive_values,
            ),
            error_message=None,
        )
    )
    await session.commit()
    return _response(result)


def _deepseek_config(settings: Settings) -> DeepSeekConfig:
    return DeepSeekConfig(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        light_model=settings.deepseek_light_model,
        standard_model=settings.deepseek_standard_model,
        timeout_seconds=settings.model_gateway_timeout_seconds,
        retry_attempts=settings.model_gateway_retry_attempts,
    )


def _response(result: GatewayResult) -> ModelChatResponse:
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


def _app_error(
    error: ModelGatewayError,
    sensitive_values: Iterable[str | None],
) -> AppError:
    return AppError(
        code=error.code,
        message=sanitize_text(error.message, extra_sensitive_values=sensitive_values),
        status_code=error.status_code,
    )


def _sensitive_values(settings: Settings) -> tuple[str | None, ...]:
    return (
        settings.deepseek_api_key,
        settings.feishu_webhook_verification_token,
        settings.feishu_webhook_signing_secret,
    )
