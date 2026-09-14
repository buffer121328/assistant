from __future__ import annotations

from typing import Any

import httpx
from tavily import AsyncTavilyClient

from domain.policies.redaction import sanitize_text

from .errors import TavilyClientError
from .types import TavilyConfig, TavilySearchRequest


class TavilyApiClient:
    """通过 Tavily 官方 Python SDK 执行搜索。"""

    def __init__(
        self,
        config: TavilyConfig,
        *,
        client: AsyncTavilyClient | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            config: 用于执行当前操作的 config 参数。
            client: 用于执行当前操作的 client 参数。
        """
        self.config = config
        self._client = client or AsyncTavilyClient(api_key=config.api_key)

    async def search(self, request: TavilySearchRequest) -> dict[str, Any]:
        """搜索。

        Args:
            request: 当前操作的结构化请求。
        """
        try:
            data = await self._client.search(
                request.query,
                max_results=request.max_results,
                include_answer=False,
                search_depth="basic",
                timeout=self.config.timeout_seconds,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise TavilyClientError("Tavily search request timed out") from exc
        except Exception as exc:
            raise TavilyClientError(self._safe_error(exc)) from exc

        if not isinstance(data, dict):
            raise TavilyClientError("Tavily search returned invalid response shape")
        return data

    def _safe_error(self, value: object) -> str:
        """执行 处理 safe error 的内部辅助逻辑。

        Args:
            value: 待校验、归一化或转换的输入值。
        """
        return sanitize_text(value, extra_sensitive_values=[self.config.api_key])
