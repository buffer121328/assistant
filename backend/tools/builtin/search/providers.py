from __future__ import annotations

from collections.abc import Iterable

import httpx

from domain.policies.redaction import sanitize_text

from .constants import PROVIDER_BRAVE, PROVIDER_DUCKDUCKGO, PROVIDER_TAVILY
from .errors import SearchProviderError
from .normalizers import (
    normalize_brave_sources,
    normalize_duckduckgo_sources,
    normalize_tavily_sources,
)
from .protocols import TavilyClientProtocol
from .types import NormalizedSearchSource, TavilySearchRequest


class TavilySearchProvider:
    """表示 处理 tavily search provider 的后端数据结构或服务对象。"""

    name = PROVIDER_TAVILY

    def __init__(
        self,
        *,
        client: TavilyClientProtocol,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        """初始化对象实例。

        Args:
            client: client 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.client = client
        self.sensitive_values = tuple(sensitive_values)

    async def search(
        self, request: TavilySearchRequest
    ) -> list[NormalizedSearchSource]:
        """搜索。

        Args:
            request: request 参数。
        """
        payload = await self.client.search(request)
        return normalize_tavily_sources(
            payload,
            extra_sensitive_values=self.sensitive_values,
        )


class BraveSearchProvider:
    """表示 处理 brave search provider 的后端数据结构或服务对象。"""

    name = PROVIDER_BRAVE

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        """初始化对象实例。

        Args:
            api_key: api_key 参数。
            base_url: base_url 参数。
            timeout_seconds: timeout_seconds 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.sensitive_values = (api_key, *tuple(sensitive_values))

    async def search(
        self, request: TavilySearchRequest
    ) -> list[NormalizedSearchSource]:
        """搜索。

        Args:
            request: request 参数。
        """
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }
        params: dict[str, str | int] = {
            "q": request.query,
            "count": request.max_results,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    self.base_url, headers=headers, params=params
                )
        except httpx.TimeoutException as exc:
            raise SearchProviderError("Brave search request timed out") from exc
        except httpx.TransportError as exc:
            raise SearchProviderError(self._safe_error(exc)) from exc

        if response.status_code >= 400:
            raise SearchProviderError(self._safe_error(response.text))
        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchProviderError("Brave search returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SearchProviderError("Brave search returned invalid response shape")
        return normalize_brave_sources(
            payload,
            extra_sensitive_values=self.sensitive_values,
        )

    def _safe_error(self, value: object) -> str:
        """执行 处理 safe error 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        return sanitize_text(value, extra_sensitive_values=self.sensitive_values)


class DuckDuckGoSearchProvider:
    """表示 处理 duck duck go search provider 的后端数据结构或服务对象。"""

    name = PROVIDER_DUCKDUCKGO

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        """初始化对象实例。

        Args:
            base_url: base_url 参数。
            timeout_seconds: timeout_seconds 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.sensitive_values = tuple(sensitive_values)

    async def search(
        self, request: TavilySearchRequest
    ) -> list[NormalizedSearchSource]:
        """搜索。

        Args:
            request: request 参数。
        """
        params: dict[str, str] = {
            "q": request.query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(self.base_url, params=params)
        except httpx.TimeoutException as exc:
            raise SearchProviderError("DuckDuckGo search request timed out") from exc
        except httpx.TransportError as exc:
            raise SearchProviderError(self._safe_error(exc)) from exc

        if response.status_code >= 400:
            raise SearchProviderError(self._safe_error(response.text))
        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchProviderError(
                "DuckDuckGo search returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise SearchProviderError(
                "DuckDuckGo search returned invalid response shape"
            )
        return normalize_duckduckgo_sources(
            payload,
            extra_sensitive_values=self.sensitive_values,
            max_results=request.max_results,
        )

    def _safe_error(self, value: object) -> str:
        """执行 处理 safe error 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        return sanitize_text(value, extra_sensitive_values=self.sensitive_values)
