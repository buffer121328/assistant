from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    GatewayRequest,
    GatewayResult,
    ModelGatewayError,
    build_error_summary,
    build_request_summary,
    build_response_summary,
    route_model,
)
from infrastructure.config import Settings
from infrastructure.repositories import ModelLogCreate, ModelLogRepository


async def handle_model_chat(
    *,
    request: GatewayRequest,
    session: AsyncSession,
    settings: Settings,
) -> GatewayResult:
    """Execute an internal model gateway chat request and persist model logs."""
    sensitive_values = _sensitive_values(settings)
    resolved_model_class = route_model(
        request.task_type,
        request.model_class,
    )

    request_summary = build_request_summary(
        request,
        resolved_model_class=resolved_model_class,
        extra_sensitive_values=sensitive_values,
    )
    from models.pool_factory import build_pooled_models

    adapter = build_pooled_models(settings)
    repository = ModelLogRepository(session)

    try:
        result = await adapter.chat(request, resolved_model_class)
    except ModelGatewayError as exc:
        await repository.create_model_log(
            ModelLogCreate(
                task_id=request.task_id,
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
        raise

    await repository.create_model_log(
        ModelLogCreate(
            task_id=request.task_id,
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
    return result


def _sensitive_values(settings: Settings) -> tuple[str | None, ...]:
    """Return model-provider sensitive settings that must be redacted."""
    return (settings.deepseek_api_key,)
