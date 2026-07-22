from __future__ import annotations

from common.redaction import sanitize_text
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from .core import (
    MODEL_CLASS_LIGHT,
    MODEL_CLASS_STANDARD,
    MODEL_GATEWAY_PROVIDER_ERROR,
    MODEL_GATEWAY_TIMEOUT,
    PROVIDER_DEEPSEEK,
    GatewayRequest,
    GatewayResult,
    GatewayUsage,
    ModelGatewayError,
)


@dataclass(frozen=True)
class DeepSeekConfig:
    """表示 处理 deep seek config 的后端数据结构或服务对象。"""

    api_key: str
    base_url: str
    light_model: str
    standard_model: str
    timeout_seconds: float
    retry_attempts: int


class DeepSeekAdapter:
    """表示 处理 deep seek adapter 的后端数据结构或服务对象。"""

    def __init__(self, config: DeepSeekConfig) -> None:
        """初始化对象实例。

        Args:
            config: config 参数。
        """
        self.config = config

    async def chat(self, request: GatewayRequest, model_class: str) -> GatewayResult:
        """处理 chat。

        Args:
            request: request 参数。
            model_class: model_class 参数。
        """
        model = self._model_for_class(model_class)
        payload = {
            "model": model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        start = perf_counter()
        attempts = max(1, self.config.retry_attempts)

        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            for attempt_index in range(attempts):
                try:
                    response = await client.post(
                        self._chat_completions_url(),
                        headers=headers,
                        json=payload,
                    )
                except httpx.TimeoutException as exc:
                    if attempt_index < attempts - 1:
                        continue
                    raise self._timeout_error(exc) from exc
                except httpx.TransportError as exc:
                    if attempt_index < attempts - 1:
                        continue
                    raise self._provider_error(str(exc)) from exc

                if response.status_code < 400:
                    return self._map_success(response, model, start)

                if (
                    _is_retryable_status(response.status_code)
                    and attempt_index < attempts - 1
                ):
                    continue

                raise self._provider_error(response.text)

        raise self._provider_error("Provider request did not return a response")

    def _chat_completions_url(self) -> str:
        """执行 处理 chat completions url 的内部辅助逻辑。"""
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    def _model_for_class(self, model_class: str) -> str:
        """执行 处理 model for class 的内部辅助逻辑。

        Args:
            model_class: model_class 参数。
        """
        if model_class == MODEL_CLASS_LIGHT:
            return self.config.light_model
        if model_class == MODEL_CLASS_STANDARD:
            return self.config.standard_model
        raise ModelGatewayError(
            code=MODEL_GATEWAY_PROVIDER_ERROR,
            message="Unsupported provider model class",
            status_code=502,
        )

    def _map_success(
        self,
        response: httpx.Response,
        model: str,
        start: float,
    ) -> GatewayResult:
        """执行 处理 map success 的内部辅助逻辑。

        Args:
            response: response 参数。
            model: model 参数。
            start: start 参数。
        """
        try:
            payload = response.json()
            content = _extract_content(payload)
            usage = _extract_usage(payload)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise self._provider_error(f"Invalid provider response: {exc}") from exc

        return GatewayResult(
            provider=PROVIDER_DEEPSEEK,
            model=model,
            content=content,
            usage=usage,
            latency_ms=max(0, round((perf_counter() - start) * 1000)),
        )

    def _timeout_error(self, _error: Exception) -> ModelGatewayError:
        """执行 处理 timeout error 的内部辅助逻辑。

        Args:
            _error: _error 参数。
        """
        return ModelGatewayError(
            code=MODEL_GATEWAY_TIMEOUT,
            message="Model provider timed out",
            status_code=504,
        )

    def _provider_error(self, detail: str) -> ModelGatewayError:
        """执行 处理 provider error 的内部辅助逻辑。

        Args:
            detail: detail 参数。
        """
        sanitized = sanitize_text(
            detail,
            extra_sensitive_values=[self.config.api_key],
        )
        return ModelGatewayError(
            code=MODEL_GATEWAY_PROVIDER_ERROR,
            message=f"Model provider request failed: {sanitized}",
            status_code=502,
        )


def _extract_content(payload: dict[str, Any]) -> str:
    """执行 提取 content 的内部辅助逻辑。

    Args:
        payload: payload 参数。
    """
    choices = payload["choices"]
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing choices")
    message = choices[0]["message"]
    content = message["content"]
    if not isinstance(content, str):
        raise TypeError("content is not text")
    return content


def _extract_usage(payload: dict[str, Any]) -> GatewayUsage:
    """执行 提取 usage 的内部辅助逻辑。

    Args:
        payload: payload 参数。
    """
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    return GatewayUsage(
        input_tokens=_usage_value(usage, "prompt_tokens", "input_tokens"),
        output_tokens=_usage_value(usage, "completion_tokens", "output_tokens"),
    )


def _usage_value(
    usage: dict[str, Any],
    provider_key: str,
    fallback_key: str,
) -> int:
    """执行 处理 usage value 的内部辅助逻辑。

    Args:
        usage: usage 参数。
        provider_key: provider_key 参数。
        fallback_key: fallback_key 参数。
    """
    value = usage.get(provider_key, usage.get(fallback_key, 0))
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _is_retryable_status(status_code: int) -> bool:
    """执行 处理 is retryable status 的内部辅助逻辑。

    Args:
        status_code: status_code 参数。
    """
    return status_code in {408, 429} or status_code >= 500
