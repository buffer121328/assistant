from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from agent.governance.routing import (
    AgentRouteModelError,
    InvalidAgentRouteDecisionError,
    build_agent_route_candidates,
    build_agent_route_messages,
    parse_agent_route_decision,
)
from capabilities import CapabilityRegistry
from model_gateway import (
    DeepSeekAdapter,
    DeepSeekConfig,
    GatewayRequest,
    GatewayResult,
    build_error_summary,
    build_request_summary,
    build_response_summary,
    route_model,
)

from infrastructure.config import Settings
from domain.models import Task
from infrastructure.repositories import ModelLogCreate, ModelLogRepository


class RoutingModelAdapter(Protocol):
    async def chat(self, request: GatewayRequest, model_class: str) -> GatewayResult: ...


class CapabilityRoutingService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        registry: CapabilityRegistry,
        adapter: RoutingModelAdapter | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.registry = registry
        self.adapter = adapter or DeepSeekAdapter(_deepseek_config(settings))
        self.repository = ModelLogRepository(session)
        self.sensitive_values = _sensitive_values(settings)

    async def route_task(self, task: Task) -> Task:
        if task.task_type != "agent":
            return task

        candidates = build_agent_route_candidates(self.registry)
        request = GatewayRequest(
            user_id=task.user_id,
            task_id=task.id,
            task_type="router",
            model_class=None,
            messages=build_agent_route_messages(
                input_text=task.input_text,
                candidates=candidates,
            ),
            temperature=0.0,
            max_tokens=256,
        )
        model_class = route_model(request.task_type, request.model_class)
        request_summary = build_request_summary(
            request,
            resolved_model_class=model_class,
            extra_sensitive_values=self.sensitive_values,
        )

        try:
            result = await self.adapter.chat(request, model_class)
        except Exception as exc:
            error = AgentRouteModelError("Agent route model request failed")
            await self._record_failure(
                task=task,
                model_class=model_class,
                request_summary=request_summary,
                error=error,
            )
            raise error from exc

        try:
            decision = parse_agent_route_decision(result.content, candidates)
        except InvalidAgentRouteDecisionError as error:
            await self._record_failure(
                task=task,
                model_class=model_class,
                request_summary=request_summary,
                error=error,
            )
            raise

        task.task_type = decision.task_type
        await self.repository.create_model_log(
            ModelLogCreate(
                task_id=task.id,
                model_class=model_class,
                request_text=request_summary,
                response_text=build_response_summary(
                    result,
                    extra_sensitive_values=self.sensitive_values,
                ),
                error_message=None,
            )
        )
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def _record_failure(
        self,
        *,
        task: Task,
        model_class: str,
        request_summary: str,
        error: Exception,
    ) -> None:
        await self.repository.create_model_log(
            ModelLogCreate(
                task_id=task.id,
                model_class=model_class,
                request_text=request_summary,
                response_text=None,
                error_message=build_error_summary(
                    error,
                    extra_sensitive_values=self.sensitive_values,
                ),
            )
        )
        await self.session.commit()


def _deepseek_config(settings: Settings) -> DeepSeekConfig:
    return DeepSeekConfig(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        light_model=settings.deepseek_light_model,
        standard_model=settings.deepseek_standard_model,
        timeout_seconds=settings.model_gateway_timeout_seconds,
        retry_attempts=settings.model_gateway_retry_attempts,
    )


def _sensitive_values(settings: Settings) -> tuple[str | None, ...]:
    return (
        settings.langbot_webhook_secret,
        settings.langbot_api_base_url,
        settings.langbot_api_key,
        settings.deepseek_api_key,
        settings.deepseek_base_url,
        settings.tavily_base_url,
        settings.tavily_api_key,
    )
