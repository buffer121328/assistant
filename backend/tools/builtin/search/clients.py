from __future__ import annotations

from typing import Any

import httpx

from domain.policies.redaction import sanitize_text

from .errors import TavilyClientError
from .types import TavilyConfig, TavilySearchRequest


class TavilyApiClient:
    """表示 处理 tavily api client 的后端数据结构或服务对象。"""

    def __init__(self, config: TavilyConfig) -> None:
        """初始化对象实例。

        Args:
            config: config 参数。
        """
        self.config = config

    async def search(self, request: TavilySearchRequest) -> dict[str, Any]:
        """搜索。

        Args:
            request: request 参数。
        """
        payload = {
            "api_key": self.config.api_key,
            "query": request.query,
            "max_results": request.max_results,
            "include_answer": False,
            "search_depth": "basic",
        }
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(
                    f"{self.config.base_url.rstrip('/')}/search",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise TavilyClientError("Tavily search request timed out") from exc
        except httpx.TransportError as exc:
            raise TavilyClientError(self._safe_error(exc)) from exc

        if response.status_code >= 400:
            raise TavilyClientError(self._safe_error(response.text))

        try:
            data = response.json()
        except ValueError as exc:
            raise TavilyClientError("Tavily search returned invalid JSON") from exc

        if not isinstance(data, dict):
            raise TavilyClientError("Tavily search returned invalid response shape")
        return data

    def _safe_error(self, value: object) -> str:
        """执行 处理 safe error 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        return sanitize_text(value, extra_sensitive_values=[self.config.api_key])
