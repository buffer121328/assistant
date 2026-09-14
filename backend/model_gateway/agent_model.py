from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Protocol, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from runtime.budget import BudgetExceededError, RunBudget
from runtime.conversation_budget import (
    ConversationTokenBudget,
    ConversationTokenReservation,
)
from memory.working_set import estimate_tokens

from agent.prompting.model_contract import (
    AgentDecision,
    AgentDecisionError,
    AgentModelRequest,
    ReviewDecision,
    WorkPlan,
    parse_agent_decision,
    parse_review_decision,
    parse_work_plan,
)
from model_gateway import (
    DeepSeekConfig,
    GatewayMessage,
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
from application.task_execution.events import TASK_EVENT_CONTENT_DELTA


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
_STRUCTURED_RECOVERY_ATTEMPTS = 1
_PHASE_MAX_TOKENS = {
    "plan": 2048,
    "decision": 4096,
    "review": 1024,
}
_PUBLIC_PHASES = {
    "plan": "planning",
    "decision": "answer_generation",
    "review": "review",
}


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
            max_tokens=_PHASE_MAX_TOKENS.get(phase, 1024),
        )
        model_class = route_model(
            gateway_request.task_type,
            gateway_request.model_class,
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
                "prompt_fingerprint": request.metadata.get("prompt_fingerprint"),
            },
            model=model_class,
            user_id=request.user_id,
            session_id=request.task_id,
            tags=(request.task_type, phase),
            version=str(request.metadata.get("prompt_fingerprint", ""))[:12] or None,
            model_parameters={
                "temperature": gateway_request.temperature,
                "max_tokens": gateway_request.max_tokens,
            },
            prompt=request.metadata,
        ) as observation:
            await self._publish_phase_event("started", phase=phase, attempt=1)
            active_request = gateway_request
            total_input_tokens = 0
            total_output_tokens = 0
            max_attempts = 1 + _STRUCTURED_RECOVERY_ATTEMPTS

            for attempt in range(1, max_attempts + 1):
                request_summary = build_request_summary(
                    active_request,
                    resolved_model_class=model_class,
                    extra_sensitive_values=self.sensitive_values,
                )
                conversation_reservation: ConversationTokenReservation | None = None
                try:
                    if self.budget is not None:
                        self.budget.check_can_continue()
                    if request.conversation_id is not None:
                        conversation_reservation = await ConversationTokenBudget(
                            self.session
                        ).reserve(
                            conversation_id=request.conversation_id,
                            user_id=request.user_id,
                            input_tokens=_estimated_input_tokens(active_request),
                            output_tokens=active_request.max_tokens,
                        )
                    result = await self._request_model(
                        active_request,
                        model_class=model_class,
                        stream_answer=(attempt == 1 and request.stream_answer),
                        phase=phase,
                    )
                except BudgetExceededError as exc:
                    await self._record_failure(
                        task_id=request.task_id,
                        model_class=model_class,
                        request_summary=request_summary,
                        error=exc,
                    )
                    await self._publish_phase_event(
                        "failed",
                        phase=phase,
                        attempt=attempt,
                        reason_code="budget_exceeded",
                        retryable=False,
                    )
                    raise
                except Exception as exc:
                    if conversation_reservation is not None:
                        await ConversationTokenBudget(self.session).release(
                            conversation_reservation
                        )
                    error = AgentModelGatewayError("Agent model request failed")
                    await self._record_failure(
                        task_id=request.task_id,
                        model_class=model_class,
                        request_summary=request_summary,
                        error=error,
                    )
                    await self._publish_phase_event(
                        "failed",
                        phase=phase,
                        attempt=attempt,
                        reason_code="provider_unavailable",
                        retryable=True,
                    )
                    raise error from exc

                try:
                    if conversation_reservation is not None:
                        await ConversationTokenBudget(self.session).settle(
                            conversation_reservation,
                            input_tokens=result.usage.input_tokens,
                            output_tokens=result.usage.output_tokens,
                        )
                    if self.budget is not None:
                        self.budget.record_model_usage(
                            input_tokens=result.usage.input_tokens,
                            output_tokens=result.usage.output_tokens,
                        )
                except BudgetExceededError as exc:
                    await self._record_failure(
                        task_id=request.task_id,
                        model_class=model_class,
                        request_summary=request_summary,
                        error=exc,
                        result=result,
                        protect_content=_protect_failure_content(request, phase),
                    )
                    await self._publish_phase_event(
                        "failed",
                        phase=phase,
                        attempt=attempt,
                        reason_code="budget_exceeded",
                        retryable=False,
                        result=result,
                    )
                    raise

                total_input_tokens += result.usage.input_tokens
                total_output_tokens += result.usage.output_tokens
                try:
                    parsed = parser(result.content)
                except AgentDecisionError as exc:
                    reason_code = _structured_failure_reason(result, error=exc)
                    await self._record_failure(
                        task_id=request.task_id,
                        model_class=model_class,
                        request_summary=request_summary,
                        error=exc,
                        result=result,
                        protect_content=_protect_failure_content(request, phase),
                    )
                    if attempt < max_attempts:
                        await self._publish_phase_event(
                            "retrying",
                            phase=phase,
                            attempt=attempt + 1,
                            reason_code=reason_code,
                            retryable=True,
                            result=result,
                        )
                        active_request = _structured_recovery_request(
                            gateway_request,
                            phase=phase,
                        )
                        continue
                    await self._publish_phase_event(
                        "failed",
                        phase=phase,
                        attempt=attempt,
                        reason_code=reason_code,
                        retryable=True,
                        result=result,
                    )
                    raise
                except Exception as exc:
                    await self._record_failure(
                        task_id=request.task_id,
                        model_class=model_class,
                        request_summary=request_summary,
                        error=exc,
                        result=result,
                        protect_content=_protect_failure_content(request, phase),
                    )
                    await self._publish_phase_event(
                        "failed",
                        phase=phase,
                        attempt=attempt,
                        reason_code="model_contract_error",
                        retryable=False,
                        result=result,
                    )
                    raise

                logged_result = result
                if (
                    request.metadata.get("protect_final_answer_logs")
                    and phase == "decision"
                    and getattr(parsed, "action", None) == "final"
                ):
                    logged_result = replace(
                        result, content="[CONTENT_WITHHELD_BY_GOVERNANCE]"
                    )
                await self.repository.create_model_log(
                    ModelLogCreate(
                        task_id=request.task_id,
                        agent_run_id=self.agent_run_id,
                        model_class=model_class,
                        request_text=request_summary,
                        response_text=build_response_summary(
                            logged_result,
                            extra_sensitive_values=self.sensitive_values,
                        ),
                        error_message=None,
                    )
                )
                await self.session.flush()
                await self._publish_phase_event(
                    "completed",
                    phase=phase,
                    attempt=attempt,
                    result=result,
                )
                usage_details = {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens,
                    "attempts": attempt,
                }
                observation.update(
                    output={"phase": phase},
                    metadata=usage_details,
                    usage_details=usage_details,
                )
                return parsed

            raise AssertionError("structured model attempts exhausted")

    async def _request_model(
        self,
        request: GatewayRequest,
        *,
        model_class: str,
        stream_answer: bool,
        phase: str,
    ) -> GatewayResult:
        """Execute one model attempt without conflating transport and contract retries.

        Args:
            request: 当前模型请求。
            model_class: 已解析的模型等级。
            stream_answer: 是否允许本次决策流式发布最终回答片段。
            phase: 当前结构化模型阶段。
        """
        if (
            phase == "decision"
            and stream_answer
            and self.event_sink is not None
            and hasattr(self.adapter, "chat_stream")
        ):
            decoder = FinalAnswerDeltaDecoder()
            event_sink = self.event_sink

            async def on_delta(chunk: str) -> None:
                """Publish only decoded final-answer text from a streaming JSON response.

                Args:
                    chunk: 当前供应商流式响应片段。
                """
                answer_delta = decoder.feed(chunk)
                if answer_delta:
                    await event_sink(TASK_EVENT_CONTENT_DELTA, {"text": answer_delta})

            return await self.adapter.chat_stream(request, model_class, on_delta)
        return await self.adapter.chat(request, model_class)

    async def _publish_phase_event(
        self,
        state: str,
        *,
        phase: str,
        attempt: int,
        reason_code: str | None = None,
        retryable: bool | None = None,
        result: GatewayResult | None = None,
    ) -> None:
        """Publish one bounded employee-safe model phase fact best-effort.

        Args:
            state: 阶段状态后缀。
            phase: 内部模型阶段。
            attempt: 当前结构化尝试序号。
            reason_code: 可选稳定失败原因码。
            retryable: 可选重试建议。
            result: 可选供应商结果，仅提取安全完成元数据。
        """
        if self.event_sink is None:
            return
        payload: dict[str, object] = {
            "phase": _PUBLIC_PHASES.get(phase, phase),
            "attempt": attempt,
        }
        if reason_code is not None:
            payload["reason_code"] = reason_code
        if retryable is not None:
            payload["retryable"] = retryable
        finish_reason = _finish_reason(result)
        if finish_reason is not None:
            payload["finish_reason"] = finish_reason
        try:
            await self.event_sink(f"task.phase.{state}", payload)
        except Exception:
            return

    async def _record_failure(
        self,
        *,
        task_id: str,
        model_class: str,
        request_summary: str,
        error: Exception,
        result: GatewayResult | None = None,
        protect_content: bool = False,
    ) -> None:
        """执行 记录 failure 的内部辅助逻辑。

        Args:
            task_id: task_id 参数。
            model_class: model_class 参数。
            request_summary: request_summary 参数。
            error: error 参数。
            result: 可选供应商结果，用于保存脱敏且有界的响应摘要。
            protect_content: 是否按内容治理规则隐藏失败响应正文。
        """
        logged_result = (
            replace(result, content="[INVALID_CONTENT_WITHHELD_BY_GOVERNANCE]")
            if result is not None and protect_content
            else result
        )
        await self.repository.create_model_log(
            ModelLogCreate(
                task_id=task_id,
                agent_run_id=self.agent_run_id,
                model_class=model_class,
                request_text=request_summary,
                response_text=(
                    build_response_summary(
                        logged_result,
                        extra_sensitive_values=self.sensitive_values,
                    )
                    if logged_result is not None
                    else None
                ),
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


def _estimated_input_tokens(request: GatewayRequest) -> int:
    """为会话预算预留当前模型请求的输入空间。"""
    return sum(estimate_tokens(message.content) for message in request.messages)


def _structured_recovery_request(
    request: GatewayRequest,
    *,
    phase: str,
) -> GatewayRequest:
    """Create one bounded regeneration request after strict contract parsing fails.

    Args:
        request: 原始模型请求。
        phase: 当前结构化模型阶段。
    """
    instruction = (
        f"上一条 {phase} 输出无法通过结构校验。重新完成同一请求，只输出一个合法 JSON 对象；"
        "不要输出代码块、说明文字或隐式思维链。不得从上一条无效输出执行或推断任何工具调用。"
    )
    return replace(
        request,
        messages=(
            *request.messages,
            GatewayMessage(role="system", content=instruction),
        ),
    )


def _protect_failure_content(request: AgentModelRequest, phase: str) -> bool:
    """Honor final-answer governance when a decision response fails validation.

    Args:
        request: 当前 Agent 模型请求。
        phase: 当前结构化模型阶段。
    """
    return bool(
        phase == "decision" and request.metadata.get("protect_final_answer_logs")
    )


def _finish_reason(result: GatewayResult | None) -> str | None:
    """Return a bounded provider completion reason when one is available.

    Args:
        result: 可选模型供应商结果。
    """
    if result is None or not isinstance(result.diagnostics, dict):
        return None
    value = result.diagnostics.get("finish_reason")
    return value[:64] if isinstance(value, str) and value else None


def _structured_failure_reason(
    result: GatewayResult,
    *,
    error: AgentDecisionError,
) -> str:
    """Classify structured failures by safe parser boundary without exposing content.

    Args:
        result: 模型供应商结果。
        error: 已分类的结构化响应解析错误。
    """
    finish_reason = (_finish_reason(result) or "").lower()
    if finish_reason in {"length", "max_tokens", "max_output_tokens"}:
        return "truncated_structured_output"
    if not result.content.strip():
        return "empty_structured_output"
    if error.failure_category == "json_extraction":
        return "structured_json_extraction_failed"
    return "structured_decision_contract_invalid"


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
        settings.qwen_api_key,
        settings.qwen_base_url,
        settings.tavily_api_key,
    )
