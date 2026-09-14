from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .chain import SearchProviderChain
from .clients import TavilyApiClient
from .constants import (
    DEFAULT_SEARCH_PROVIDER_ORDER,
    PROVIDER_EXA,
    PROVIDER_SERPER,
    PROVIDER_TAVILY,
)
from .protocols import SearchProvider, TavilyClientProtocol
from .providers import (
    ExaSearchProvider,
    SerperSearchProvider,
    TavilySearchProvider,
)
from .types import TavilyConfig


def build_tavily_config(settings: Any) -> TavilyConfig:
    """构建 tavily config。

    Args:
        settings: 用于执行当前操作的 settings 参数。
    """
    timeout = getattr(settings, "search_provider_timeout_seconds", None)
    return TavilyConfig(
        api_key=settings.tavily_api_key,
        timeout_seconds=settings.tavily_timeout_seconds,
        max_results=settings.tavily_max_results,
        provider_order=parse_search_provider_order(
            getattr(settings, "search_provider_order", DEFAULT_SEARCH_PROVIDER_ORDER)
        ),
        serper_api_key=getattr(settings, "serper_api_key", ""),
        serper_base_url=getattr(
            settings,
            "serper_base_url",
            "https://google.serper.dev/search",
        ),
        exa_api_key=getattr(settings, "exa_api_key", ""),
        exa_base_url=getattr(settings, "exa_base_url", "https://api.exa.ai/search"),
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
        config: 用于执行当前操作的 config 参数。
        tavily_client: 用于执行当前操作的 tavily client 参数。
        sensitive_values: 用于执行当前操作的 sensitive values 参数。
    """
    extra_sensitive_values = (
        config.api_key,
        config.serper_api_key,
        config.exa_api_key,
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
        elif provider_name == PROVIDER_SERPER and config.serper_api_key:
            providers.append(
                SerperSearchProvider(
                    api_key=config.serper_api_key,
                    base_url=config.serper_base_url,
                    timeout_seconds=config.effective_provider_timeout_seconds,
                    sensitive_values=extra_sensitive_values,
                )
            )
        elif provider_name == PROVIDER_EXA and config.exa_api_key:
            providers.append(
                ExaSearchProvider(
                    api_key=config.exa_api_key,
                    base_url=config.exa_base_url,
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
        value: 待校验、归一化或转换的输入值。
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
