from importlib import import_module


EXPECTED_EXPORTS = {
    "DEFAULT_SEARCH_PROVIDER_ORDER",
    "ExaSearchProvider",
    "NormalizedSearchSource",
    "PROVIDER_EXA",
    "PROVIDER_SERPER",
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
    "SerperSearchProvider",
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
    "normalize_exa_sources",
    "normalize_serper_sources",
    "normalize_tavily_sources",
    "parse_search_provider_order",
}


def test_builtin_search_public_exports_remain_available() -> None:
    """Search tool symbols remain available from tools.builtin.search."""
    search = import_module("tools.builtin.search")

    assert set(search.__all__) == EXPECTED_EXPORTS
    for name in EXPECTED_EXPORTS:
        assert getattr(search, name) is not None
