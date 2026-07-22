from __future__ import annotations

from common.redaction import sanitize_text
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any

import httpx

from .core import (
    GatewayRequest,
    GatewayResult,
    GatewayUsage,
    MODEL_GATEWAY_PROVIDER_ERROR,
    MODEL_GATEWAY_TIMEOUT,
    ModelGatewayError,
)
from .pools import ModelNode


class OpenAICompatibleAdapter:
    """表示 处理 open aicompatible adapter 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        retry_attempts: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            timeout_seconds: timeout_seconds 参数。
            retry_attempts: retry_attempts 参数。
            transport: transport 参数。
        """
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = max(1, retry_attempts)
        self.transport = transport

    async def chat(self, request: GatewayRequest, node: ModelNode) -> GatewayResult:
        """处理 chat。

        Args:
            request: request 参数。
            node: node 参数。
        """
        started = perf_counter()
        response = await self._post(request, node, stream=False)
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content is not text")
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise self._provider_error(
                node, f"Invalid provider response: {exc}"
            ) from exc
        return GatewayResult(
            provider=node.provider,
            model=node.model,
            content=content,
            usage=_usage(payload),
            latency_ms=_elapsed(started),
        )

    async def stream_chat(
        self, request: GatewayRequest, node: ModelNode
    ) -> AsyncIterator[str]:
        """处理 stream chat。

        Args:
            request: request 参数。
            node: node 参数。
        """
        payload = _payload(request, node.model, stream=True)
        headers = _headers(node.api_key)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                async with client.stream(
                    "POST", _url(node), headers=headers, json=payload
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise self._provider_error(node, body.decode(errors="replace"))
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            item = httpx.Response(200, content=data).json()
                            content = item["choices"][0]["delta"].get("content")
                        except (ValueError, TypeError, KeyError, IndexError) as exc:
                            raise self._provider_error(
                                node, "Invalid provider stream"
                            ) from exc
                        if isinstance(content, str) and content:
                            yield content
        except httpx.TimeoutException as exc:
            raise ModelGatewayError(
                MODEL_GATEWAY_TIMEOUT, "Model provider timed out", 504
            ) from exc
        except httpx.TransportError as exc:
            raise self._provider_error(node, str(exc)) from exc

    async def _post(
        self, request: GatewayRequest, node: ModelNode, *, stream: bool
    ) -> httpx.Response:
        """执行 处理 post 的内部辅助逻辑。

        Args:
            request: request 参数。
            node: node 参数。
            stream: stream 参数。
        """
        last_error: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, transport=self.transport
        ) as client:
            for attempt in range(self.retry_attempts):
                try:
                    response = await client.post(
                        _url(node),
                        headers=_headers(node.api_key),
                        json=_payload(request, node.model, stream=stream),
                    )
                except httpx.TimeoutException as exc:
                    last_error = exc
                    if attempt + 1 < self.retry_attempts:
                        continue
                    raise ModelGatewayError(
                        MODEL_GATEWAY_TIMEOUT, "Model provider timed out", 504
                    ) from exc
                except httpx.TransportError as exc:
                    last_error = exc
                    if attempt + 1 < self.retry_attempts:
                        continue
                    raise self._provider_error(node, str(exc)) from exc
                if response.status_code < 400:
                    return response
                last_error = RuntimeError(response.text)
                if (
                    response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
                    and attempt + 1 < self.retry_attempts
                ):
                    continue
                raise self._provider_error(node, response.text)
        raise self._provider_error(node, str(last_error or "provider failed"))

    def _provider_error(self, node: ModelNode, detail: str) -> ModelGatewayError:
        """执行 处理 provider error 的内部辅助逻辑。

        Args:
            node: node 参数。
            detail: detail 参数。
        """
        safe = sanitize_text(
            detail, extra_sensitive_values=(node.api_key, node.base_url)
        )
        return ModelGatewayError(
            MODEL_GATEWAY_PROVIDER_ERROR,
            f"Model provider request failed: {safe}",
            502,
        )


def _payload(request: GatewayRequest, model: str, *, stream: bool) -> dict[str, Any]:
    """执行 处理 payload 的内部辅助逻辑。

    Args:
        request: request 参数。
        model: model 参数。
        stream: stream 参数。
    """
    return {
        "model": model,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "stream": stream,
    }


def _headers(api_key: str) -> dict[str, str]:
    """执行 处理 headers 的内部辅助逻辑。

    Args:
        api_key: api_key 参数。
    """
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _url(node: ModelNode) -> str:
    """执行 处理 url 的内部辅助逻辑。

    Args:
        node: node 参数。
    """
    return f"{node.base_url.rstrip('/')}/chat/completions"


def _usage(payload: dict[str, Any]) -> GatewayUsage:
    """执行 处理 usage 的内部辅助逻辑。

    Args:
        payload: payload 参数。
    """
    raw = payload.get("usage") if isinstance(payload, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    return GatewayUsage(
        input_tokens=int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0),
        output_tokens=int(
            raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0
        ),
    )


def _elapsed(started: float) -> int:
    """执行 处理 elapsed 的内部辅助逻辑。

    Args:
        started: started 参数。
    """
    return max(0, round((perf_counter() - started) * 1000))
