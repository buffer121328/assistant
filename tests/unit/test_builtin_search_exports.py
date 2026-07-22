from importlib import import_module


EXPECTED_EXPORTS = {
    "BraveSearchProvider",
    "DEFAULT_SEARCH_PROVIDER_ORDER",
    "DuckDuckGoSearchProvider",
    "NormalizedSearchSource",
    "PROVIDER_BRAVE",
    "PROVIDER_DUCKDUCKGO",
    "PROVIDER_TAVILY",
    "ProviderFailure",
    "SEARCH_WEB_TOOL_NAME",
    "SearchProvider",
    "SearchProviderChain",
    "SearchProviderChainError",
    "SearchProviderChainResult",
    "SearchProviderError",
    "SearchWebResult",
    "SearchWebTool",
    "SearchWebToolError",
    "TOOL_STATUS_FAILED",
    "TOOL_STATUS_SUCCEEDED",
    "TavilyApiClient",
    "TavilyClientError",
    "TavilyClientProtocol",
    "TavilyConfig",
    "TavilySearchProvider",
    "TavilySearchRequest",
    "build_search_provider_chain",
    "build_tavily_config",
    "normalize_brave_sources",
    "normalize_duckduckgo_sources",
    "normalize_tavily_sources",
    "parse_search_provider_order",
}


def test_builtin_search_public_exports_remain_available() -> None:
    """Search tool symbols remain available from tools.builtin.search."""
    search = import_module("tools.builtin.search")

    assert set(search.__all__) == EXPECTED_EXPORTS
    for name in EXPECTED_EXPORTS:
        assert getattr(search, name) is not None
