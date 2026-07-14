from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent_harness import (
    AgentDecision,
    AgentModelRequest,
    ReviewDecision,
    WorkPlan,
    parse_agent_decision,
    parse_review_decision,
    parse_work_plan,
)
from packages.model_gateway import (
    DeepSeekAdapter,
    DeepSeekConfig,
    GatewayRequest,
    GatewayResult,
    build_error_summary,
    build_request_summary,
    build_response_summary,
    route_model,
)
from packages.observability import NoopObservability, Observability

from .config import Settings
from .repositories import ModelLogCreate, ModelLogRepository


class AgentGatewayAdapter(Protocol):
    async def chat(self, request: GatewayRequest, model_class: str) -> GatewayResult: ...


class AgentModelGatewayError(RuntimeError):
    pass


_ModelOutput = TypeVar("_ModelOutput")


class AgentGatewayModel:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        adapter: AgentGatewayAdapter | None = None,
        observability: Observability | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.adapter = adapter or DeepSeekAdapter(_deepseek_config(settings))
        self.repository = ModelLogRepository(session)
        self.sensitive_values = _sensitive_values(settings)
        self.observability = observability or NoopObservability()

    async def decide(self, request: AgentModelRequest) -> AgentDecision:
        return await self._complete(
            request,
            phase="decision",
            parser=parse_agent_decision,
        )

    async def create_plan(self, request: AgentModelRequest) -> WorkPlan:
        return await self._complete(
            request,
            phase="plan",
            parser=parse_work_plan,
        )

    async def review(self, request: AgentModelRequest) -> ReviewDecision:
        return await self._complete(
            request,
            phase="review",
            parser=parse_review_decision,
        )

    async def _complete(
        self,
        request: AgentModelRequest,
        *,
        phase: str,
        parser: Callable[[str], _ModelOutput],
    ) -> _ModelOutput:
        gateway_request = GatewayRequest(
            user_id=request.user_id,
            task_id=request.task_id,
            task_type=_gateway_task_type(request.task_type),
            model_class=None,
            messages=request.messages,
            temperature=0.0,
            max_tokens=1024,
        )
        model_class = route_model(
            gateway_request.task_type,
            gateway_request.model_class,
        )
        request_summary = build_request_summary(
            gateway_request,
            resolved_model_class=model_class,
            extra_sensitive_values=self.sensitive_values,
        )

        with self.observability.observe(
            f"agent.model.{phase}",
            as_type="generation",
            input={
                "task_id": request.task_id,
                "task_type": request.task_type,
                "message_count": len(request.messages),
            },
            metadata={
                "task_id": request.task_id,
                "user_id": request.user_id,
                "model_class": model_class,
            },
            model=model_class,
        ) as observation:
            try:
                result = await self.adapter.chat(gateway_request, model_class)
            except Exception as exc:
                error = AgentModelGatewayError("Agent model request failed")
                await self._record_failure(
                    task_id=request.task_id,
                    model_class=model_class,
                    request_summary=request_summary,
                    error=error,
                )
                raise error from exc

            try:
                parsed = parser(result.content)
            except Exception as exc:
                await self._record_failure(
                    task_id=request.task_id,
                    model_class=model_class,
                    request_summary=request_summary,
                    error=exc,
                )
                raise

            await self.repository.create_model_log(
                ModelLogCreate(
                    task_id=request.task_id,
                    model_class=model_class,
                    request_text=request_summary,
                    response_text=build_response_summary(
                        result,
                        extra_sensitive_values=self.sensitive_values,
                    ),
                    error_message=None,
                )
            )
            await self.session.flush()
            observation.update(
                output={
                    "phase": phase,
                },
                metadata={
                    "input_tokens": result.usage.input_tokens,
                    "output_tokens": result.usage.output_tokens,
                    "total_tokens": (
                        result.usage.input_tokens + result.usage.output_tokens
                    ),
                },
            )
            return parsed

    async def _record_failure(
        self,
        *,
        task_id: str,
        model_class: str,
        request_summary: str,
        error: Exception,
    ) -> None:
        await self.repository.create_model_log(
            ModelLogCreate(
                task_id=task_id,
                model_class=model_class,
                request_text=request_summary,
                response_text=None,
                error_message=build_error_summary(
                    error,
                    extra_sensitive_values=self.sensitive_values,
                ),
            )
        )
        await self.session.flush()


def _gateway_task_type(task_type: str) -> str:
    if task_type == "office":
        return "office_text"
    return task_type


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
        settings.deepseek_api_key,
        settings.deepseek_base_url,
        settings.tavily_api_key,
        settings.tavily_base_url,
    )
