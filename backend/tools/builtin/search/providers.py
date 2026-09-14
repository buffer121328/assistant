from __future__ import annotations

from collections.abc import Iterable

import httpx

from domain.policies.redaction import sanitize_text

from .constants import PROVIDER_EXA, PROVIDER_SERPER, PROVIDER_TAVILY
from .errors import SearchProviderError
from .normalizers import (
    normalize_exa_sources,
    normalize_serper_sources,
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
            client: 用于执行当前操作的 client 参数。
            sensitive_values: 用于执行当前操作的 sensitive values 参数。
        """
        self.client = client
        self.sensitive_values = tuple(sensitive_values)

    async def search(
        self, request: TavilySearchRequest
    ) -> list[NormalizedSearchSource]:
        """搜索。

        Args:
            request: 当前操作的结构化请求。
        """
        payload = await self.client.search(request)
        return normalize_tavily_sources(
            payload,
            extra_sensitive_values=self.sensitive_values,
        )


class SerperSearchProvider:
    """通过 Serper Google Search API 执行普通网页搜索。"""

    name = PROVIDER_SERPER

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
            api_key: 用于执行当前操作的 api key 参数。
            base_url: 用于执行当前操作的 base url 参数。
            timeout_seconds: 允许的最长等待时间（秒）。
            sensitive_values: 用于执行当前操作的 sensitive values 参数。
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
            request: 当前操作的结构化请求。
        """
        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": self.api_key,
        }
        payload: dict[str, str | int] = {
            "q": request.query,
            "num": request.max_results,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise SearchProviderError("Serper search request timed out") from exc
        except httpx.TransportError as exc:
            raise SearchProviderError(self._safe_error(exc)) from exc

        if response.status_code >= 400:
            raise SearchProviderError(self._safe_error(response.text))
        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderError("Serper search returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise SearchProviderError("Serper search returned invalid response shape")
        return normalize_serper_sources(
            data,
            extra_sensitive_values=self.sensitive_values,
        )

    def _safe_error(self, value: object) -> str:
        """执行 处理 safe error 的内部辅助逻辑。

        Args:
            value: 待校验、归一化或转换的输入值。
        """
        return sanitize_text(value, extra_sensitive_values=self.sensitive_values)


class ExaSearchProvider:
    """通过 Exa Search API 执行语义网页搜索。"""

    name = PROVIDER_EXA

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
            api_key: 用于执行当前操作的 api key 参数。
            base_url: 用于执行当前操作的 base url 参数。
            timeout_seconds: 允许的最长等待时间（秒）。
            sensitive_values: 用于执行当前操作的 sensitive values 参数。
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
            request: 当前操作的结构化请求。
        """
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }
        payload: dict[str, str | int] = {
            "query": request.query,
            "numResults": request.max_results,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise SearchProviderError("Exa search request timed out") from exc
        except httpx.TransportError as exc:
            raise SearchProviderError(self._safe_error(exc)) from exc

        if response.status_code >= 400:
            raise SearchProviderError(self._safe_error(response.text))
        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderError("Exa search returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise SearchProviderError("Exa search returned invalid response shape")
        return normalize_exa_sources(
            data,
            extra_sensitive_values=self.sensitive_values,
        )

    def _safe_error(self, value: object) -> str:
        """执行 处理 safe error 的内部辅助逻辑。

        Args:
            value: 待校验、归一化或转换的输入值。
        """
        return sanitize_text(value, extra_sensitive_values=self.sensitive_values)
