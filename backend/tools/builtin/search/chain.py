from __future__ import annotations

from collections.abc import Iterable, Sequence

from domain.policies.redaction import sanitize_text

from .errors import SearchProviderChainError
from .normalizers import _dedupe_sources, _failure_category
from .protocols import SearchProvider
from .types import ProviderFailure, SearchProviderChainResult, TavilySearchRequest


class SearchProviderChain:
    """表示 搜索 provider chain 的后端数据结构或服务对象。"""

    def __init__(
        self,
        providers: Sequence[SearchProvider],
        *,
        fallback_on_empty: bool,
        max_results: int,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        """初始化对象实例。

        Args:
            providers: providers 参数。
            fallback_on_empty: fallback_on_empty 参数。
            max_results: max_results 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.providers = tuple(providers)
        self.fallback_on_empty = fallback_on_empty
        self.max_results = max_results
        self.sensitive_values = tuple(sensitive_values)

    async def search(self, request: TavilySearchRequest) -> SearchProviderChainResult:
        """搜索。

        Args:
            request: request 参数。
        """
        attempted: list[str] = []
        failures: list[ProviderFailure] = []
        fallback_reason: str | None = None
        for index, provider in enumerate(self.providers):
            attempted.append(provider.name)
            try:
                sources = _dedupe_sources(await provider.search(request))
            except Exception as exc:
                failures.append(self._failure(provider.name, exc))
                fallback_reason = "provider_failed"
                continue

            bounded_sources = sources[: self.max_results]
            has_later_provider = index < len(self.providers) - 1
            if bounded_sources or not self.fallback_on_empty or not has_later_provider:
                return SearchProviderChainResult(
                    query=request.query,
                    sources=bounded_sources,
                    attempted_providers=tuple(attempted),
                    selected_provider=provider.name,
                    failures=tuple(failures),
                    fallback_reason=fallback_reason,
                )

            failures.append(
                ProviderFailure(
                    provider=provider.name,
                    category="empty_results",
                    message="provider returned no results",
                )
            )
            fallback_reason = "empty_results"

        if not attempted:
            raise SearchProviderChainError(
                "no configured search providers are enabled",
                attempted_providers=attempted,
                failures=failures,
            )
        raise SearchProviderChainError(
            "all configured search providers failed or returned no results",
            attempted_providers=attempted,
            failures=failures,
        )

    def _failure(self, provider: str, exc: Exception) -> ProviderFailure:
        """执行 处理 failure 的内部辅助逻辑。

        Args:
            provider: provider 参数。
            exc: exc 参数。
        """
        safe_message = sanitize_text(exc, extra_sensitive_values=self.sensitive_values)
        if "traceback" in safe_message.lower():
            safe_message = "内部错误已脱敏"
        return ProviderFailure(
            provider=provider,
            category=_failure_category(exc),
            message=safe_message,
        )
