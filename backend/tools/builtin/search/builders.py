from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .chain import SearchProviderChain
from .clients import TavilyApiClient
from .constants import (
    DEFAULT_SEARCH_PROVIDER_ORDER,
    PROVIDER_BRAVE,
    PROVIDER_DUCKDUCKGO,
    PROVIDER_TAVILY,
)
from .protocols import SearchProvider, TavilyClientProtocol
from .providers import (
    BraveSearchProvider,
    DuckDuckGoSearchProvider,
    TavilySearchProvider,
)
from .types import TavilyConfig


def build_tavily_config(settings: Any) -> TavilyConfig:
    """构建 tavily config。

    Args:
        settings: settings 参数。
    """
    timeout = getattr(settings, "search_provider_timeout_seconds", None)
    return TavilyConfig(
        base_url=settings.tavily_base_url,
        api_key=settings.tavily_api_key,
        timeout_seconds=settings.tavily_timeout_seconds,
        max_results=settings.tavily_max_results,
        provider_order=parse_search_provider_order(
            getattr(settings, "search_provider_order", DEFAULT_SEARCH_PROVIDER_ORDER)
        ),
        brave_search_api_key=getattr(settings, "brave_search_api_key", ""),
        brave_search_base_url=getattr(
            settings,
            "brave_search_base_url",
            "https://api.search.brave.com/res/v1/web/search",
        ),
        duckduckgo_search_enabled=getattr(
            settings,
            "duckduckgo_search_enabled",
            False,
        ),
        duckduckgo_search_base_url=getattr(
            settings,
            "duckduckgo_search_base_url",
            "https://api.duckduckgo.com/",
        ),
        fallback_on_empty=getattr(settings, "search_fallback_on_empty", True),
        provider_timeout_seconds=timeout,
    )


def build_search_provider_chain(
    config: TavilyConfig,
    *,
    tavily_client: TavilyClientProtocol | None = None,
    sensitive_values: Iterable[str | None] = (),
) -> SearchProviderChain:
    """构建 search provider chain。

    Args:
        config: config 参数。
        tavily_client: tavily_client 参数。
        sensitive_values: sensitive_values 参数。
    """
    extra_sensitive_values = (
        config.api_key,
        config.brave_search_api_key,
        *tuple(sensitive_values),
    )
    providers: list[SearchProvider] = []
    for provider_name in config.provider_order:
        if provider_name == PROVIDER_TAVILY and config.api_key:
            providers.append(
                TavilySearchProvider(
                    client=tavily_client or TavilyApiClient(config),
                    sensitive_values=extra_sensitive_values,
                )
            )
        elif provider_name == PROVIDER_BRAVE and config.brave_search_api_key:
            providers.append(
                BraveSearchProvider(
                    api_key=config.brave_search_api_key,
                    base_url=config.brave_search_base_url,
                    timeout_seconds=config.effective_provider_timeout_seconds,
                    sensitive_values=extra_sensitive_values,
                )
            )
        elif provider_name == PROVIDER_DUCKDUCKGO and config.duckduckgo_search_enabled:
            providers.append(
                DuckDuckGoSearchProvider(
                    base_url=config.duckduckgo_search_base_url,
                    timeout_seconds=config.effective_provider_timeout_seconds,
                    sensitive_values=extra_sensitive_values,
                )
            )

    return SearchProviderChain(
        providers,
        fallback_on_empty=config.fallback_on_empty,
        max_results=config.max_results,
        sensitive_values=extra_sensitive_values,
    )


def parse_search_provider_order(value: object) -> tuple[str, ...]:
    """解析 search provider order。

    Args:
        value: value 参数。
    """
    if isinstance(value, str):
        raw_names = value.split(",")
    elif isinstance(value, Iterable):
        raw_names = list(value)
    else:
        raw_names = list(DEFAULT_SEARCH_PROVIDER_ORDER)

    names: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        name = str(raw_name).strip().lower()
        if name not in DEFAULT_SEARCH_PROVIDER_ORDER or name in seen:
            continue
        names.append(name)
        seen.add(name)
    return tuple(names) or DEFAULT_SEARCH_PROVIDER_ORDER
