from __future__ import annotations

import json
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from model_gateway import (
    DeepSeekAdapter,
    DeepSeekConfig,
    GatewayMessage,
    GatewayRequest,
    GatewayResult,
    build_error_summary,
    build_request_summary,
    build_response_summary,
)
from agent.review.core import JudgeDecision, JudgeRequest

from infrastructure.config import Settings
from infrastructure.repositories import ModelLogCreate, ModelLogRepository


class JudgeGateway(Protocol):
    async def chat(self, request: GatewayRequest, model_class: str) -> GatewayResult: ...


class GatewayJudgeModel:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        adapter: JudgeGateway | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.adapter = adapter or DeepSeekAdapter(
            DeepSeekConfig(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                light_model=settings.deepseek_light_model,
                standard_model=settings.deepseek_standard_model,
                timeout_seconds=settings.model_gateway_timeout_seconds,
                retry_attempts=settings.model_gateway_retry_attempts,
            )
        )
        self.repository = ModelLogRepository(session)
        self.sensitive_values = (
            settings.deepseek_api_key,
            settings.deepseek_base_url,
        )

    async def evaluate(self, request: JudgeRequest) -> JudgeDecision:
        gateway_request = GatewayRequest(
            user_id=request.user_id,
            task_id=request.task_id,
            task_type="research_report",
            model_class="standard",
            messages=(
                GatewayMessage(
                    role="system",
                    content=(
                        "你是输出质量评估器。按 0 到 1 评价 relevance、"
                        "completeness、faithfulness。只输出 JSON："
                        '{"relevance":0.0,"completeness":0.0,'
                        '"faithfulness":0.0,"rationale":"..."}。'
                        "不得输出凭据或额外文本。"
                    ),
                ),
                GatewayMessage(
                    role="user",
                    content=(
                        f"输入：{request.input_text}\n"
                        f"输出：{request.output_text}\n"
                        f"策略：{request.policy_version}"
                    ),
                ),
            ),
            temperature=0.0,
            max_tokens=512,
        )
        request_summary = build_request_summary(
            gateway_request,
            resolved_model_class="standard",
            extra_sensitive_values=self.sensitive_values,
        )
        try:
            result = await self.adapter.chat(gateway_request, "standard")
            decision = parse_judge_decision(result.content)
        except Exception as exc:
            await self.repository.create_model_log(
                ModelLogCreate(
                    task_id=request.task_id,
                    model_class="standard",
                    request_text=request_summary,
                    response_text=None,
                    error_message=build_error_summary(
                        exc,
                        extra_sensitive_values=self.sensitive_values,
                    ),
                )
            )
            raise
        await self.repository.create_model_log(
            ModelLogCreate(
                task_id=request.task_id,
                model_class="standard",
                request_text=request_summary,
                response_text=build_response_summary(
                    result,
                    extra_sensitive_values=self.sensitive_values,
                ),
                error_message=None,
            )
        )
        return decision


def parse_judge_decision(value: str) -> JudgeDecision:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("Judge output must be a JSON object")
    rationale = payload.get("rationale", "")
    if not isinstance(rationale, str):
        raise ValueError("Judge rationale must be text")
    return JudgeDecision(
        relevance=_score(payload.get("relevance")),
        completeness=_score(payload.get("completeness")),
        faithfulness=_score(payload.get("faithfulness")),
        rationale=rationale.strip(),
    )


def _score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Judge score must be numeric")
    return float(value)
