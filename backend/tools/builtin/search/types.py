from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import DEFAULT_SEARCH_PROVIDER_ORDER
from .utils import _truncate


@dataclass(frozen=True)
class TavilyConfig:
    """表示 处理 tavily config 的后端数据结构或服务对象。"""

    base_url: str
    api_key: str
    timeout_seconds: float
    max_results: int
    provider_order: tuple[str, ...] = DEFAULT_SEARCH_PROVIDER_ORDER
    brave_search_api_key: str = ""
    brave_search_base_url: str = "https://api.search.brave.com/res/v1/web/search"
    duckduckgo_search_enabled: bool = False
    duckduckgo_search_base_url: str = "https://api.duckduckgo.com/"
    fallback_on_empty: bool = True
    provider_timeout_seconds: float | None = None

    @property
    def effective_provider_timeout_seconds(self) -> float:
        """处理 effective provider timeout seconds。"""
        return self.provider_timeout_seconds or self.timeout_seconds


@dataclass(frozen=True)
class TavilySearchRequest:
    """表示 处理 tavily search request 的后端数据结构或服务对象。"""

    task_id: str
    user_id: str
    query: str
    max_results: int


@dataclass(frozen=True)
class NormalizedSearchSource:
    """表示 处理 normalized search source 的后端数据结构或服务对象。"""

    title: str
    url: str
    snippet: str
    provider_metadata: dict[str, Any]

    def to_workflow_dict(self) -> dict[str, Any]:
        """转换为目标格式 workflow dict。"""
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider_metadata": self.provider_metadata,
        }


@dataclass(frozen=True)
class SearchWebResult:
    """表示 搜索 web result 的后端数据结构或服务对象。"""

    query: str
    sources: list[NormalizedSearchSource]

    def to_workflow_sources(self) -> list[dict[str, Any]]:
        """转换为目标格式 workflow sources。"""
        return [source.to_workflow_dict() for source in self.sources]


@dataclass(frozen=True)
class ProviderFailure:
    """表示 处理 provider failure 的后端数据结构或服务对象。"""

    provider: str
    category: str
    message: str

    def to_log_dict(self) -> dict[str, str]:
        """转换为目标格式 log dict。"""
        return {
            "provider": self.provider,
            "category": self.category,
            "message": _truncate(self.message, limit=300),
        }


@dataclass(frozen=True)
class SearchProviderChainResult:
    """表示 搜索 provider chain result 的后端数据结构或服务对象。"""

    query: str
    sources: list[NormalizedSearchSource]
    attempted_providers: tuple[str, ...]
    selected_provider: str | None
    failures: tuple[ProviderFailure, ...]
    fallback_reason: str | None = None
