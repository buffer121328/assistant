from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from runtime.budget import BudgetExceededError, RunBudget

from agent import (
    AgentDecision,
    AgentModelRequest,
    ReviewDecision,
    WorkPlan,
    parse_agent_decision,
    parse_review_decision,
    parse_work_plan,
)
from model_gateway import (
    DeepSeekConfig,
    GatewayRequest,
    GatewayResult,
    build_error_summary,
    build_request_summary,
    build_response_summary,
    route_model,
)
from infrastructure.telemetry.observability import NoopObservability, Observability

from model_gateway.streaming import FinalAnswerDeltaDecoder
from infrastructure.settings.config import Settings
from infrastructure.repositories import ModelLogCreate, ModelLogRepository
from tasks.events import TASK_EVENT_CONTENT_DELTA


class AgentGatewayAdapter(Protocol):
    """表示 处理 agent gateway adapter 的后端数据结构或服务对象。"""

    async def chat(self, request: GatewayRequest, model_class: str) -> GatewayResult:
        """处理 chat。

        Args:
            request: request 参数。
            model_class: model_class 参数。
        """
        ...


class AgentModelGatewayError(RuntimeError):
    """表示 处理 agent model gateway error 的后端数据结构或服务对象。"""

    pass


_ModelOutput = TypeVar("_ModelOutput")


class AgentGatewayModel:
    """表示 处理 agent gateway model 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        adapter: AgentGatewayAdapter | None = None,
        agent_run_id: str | None = None,
        observability: Observability | None = None,
        event_sink: Callable[[str, dict[str, object]], Awaitable[None]] | None = None,
        budget: RunBudget | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            settings: settings 参数。
            adapter: adapter 参数。
            agent_run_id: agent_run_id 参数。
            observability: observability 参数。
            event_sink: event_sink 参数。
            budget: budget 参数。
        """
        self.session = session
        self.settings = settings
        self.agent_run_id = agent_run_id
        if adapter is None:
            from model_gateway.pool_factory import build_pooled_models

            adapter = build_pooled_models(settings)
        self.adapter = adapter
        self.repository = ModelLogRepository(session)
        self.sensitive_values = _sensitive_values(settings)
        self.observability = observability or NoopObservability()
        self.event_sink = event_sink
        self.budget = budget

    def set_run_budget(self, budget: RunBudget) -> None:
        """处理 set run budget。

        Args:
            budget: budget 参数。
        """
        self.budget = budget

    async def decide(self, request: AgentModelRequest) -> AgentDecision:
        """处理 decide。

        Args:
            request: request 参数。
        """
        return await self._complete(
            request,
            phase="decision",
            parser=parse_agent_decision,
        )

    async def create_plan(self, request: AgentModelRequest) -> WorkPlan:
        """创建 plan。

        Args:
            request: request 参数。
        """
        return await self._complete(
            request,
            phase="plan",
            parser=parse_work_plan,
        )

    async def review(self, request: AgentModelRequest) -> ReviewDecision:
        """处理 review。

        Args:
            request: request 参数。
        """
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
        """执行 处理 complete 的内部辅助逻辑。

        Args:
            request: request 参数。
            phase: phase 参数。
            parser: parser 参数。
        """
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
                if self.budget is not None:
                    self.budget.check_can_continue()
                if (
                    phase == "decision"
                    and request.stream_answer
                    and self.event_sink is not None
                    and hasattr(self.adapter, "chat_stream")
                ):
                    decoder = FinalAnswerDeltaDecoder()
                    event_sink = self.event_sink
                    assert event_sink is not None

                    async def on_delta(chunk: str) -> None:
                        """处理 on delta。

                        Args:
                            chunk: chunk 参数。
                        """
                        answer_delta = decoder.feed(chunk)
                        if answer_delta:
                            await event_sink(
                                TASK_EVENT_CONTENT_DELTA, {"text": answer_delta}
                            )

                    result = await self.adapter.chat_stream(
                        gateway_request, model_class, on_delta
                    )
                else:
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
                if self.budget is not None:
                    self.budget.record_model_usage(
                        input_tokens=result.usage.input_tokens,
                        output_tokens=result.usage.output_tokens,
                    )
                parsed = parser(result.content)
            except BudgetExceededError as exc:
                await self._record_failure(
                    task_id=request.task_id,
                    model_class=model_class,
                    request_summary=request_summary,
                    error=exc,
                )
                raise
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
                    agent_run_id=self.agent_run_id,
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
        """执行 记录 failure 的内部辅助逻辑。

        Args:
            task_id: task_id 参数。
            model_class: model_class 参数。
            request_summary: request_summary 参数。
            error: error 参数。
        """
        await self.repository.create_model_log(
            ModelLogCreate(
                task_id=task_id,
                agent_run_id=self.agent_run_id,
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
    """执行 处理 gateway task type 的内部辅助逻辑。

    Args:
        task_type: task_type 参数。
    """
    if task_type == "office":
        return "office_text"
    return task_type


def _deepseek_config(settings: Settings) -> DeepSeekConfig:
    """执行 处理 deepseek config 的内部辅助逻辑。

    Args:
        settings: settings 参数。
    """
    return DeepSeekConfig(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        light_model=settings.deepseek_light_model,
        standard_model=settings.deepseek_standard_model,
        timeout_seconds=settings.models_timeout_seconds,
        retry_attempts=settings.models_retry_attempts,
    )


def _sensitive_values(settings: Settings) -> tuple[str | None, ...]:
    """执行 处理 sensitive values 的内部辅助逻辑。

    Args:
        settings: settings 参数。
    """
    return (
        settings.deepseek_api_key,
        settings.deepseek_base_url,
        settings.tavily_api_key,
        settings.tavily_base_url,
    )
