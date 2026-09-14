from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import DEFAULT_SEARCH_PROVIDER_ORDER
from .utils import _truncate


@dataclass(frozen=True)
class TavilyConfig:
    """表示 处理 tavily config 的后端数据结构或服务对象。"""

    api_key: str  # api_key 对应的数据字段。
    timeout_seconds: float  # timeout_seconds 对应的数据字段。
    max_results: int  # max_results 对应的数据字段。
    provider_order: tuple[str, ...] = DEFAULT_SEARCH_PROVIDER_ORDER  # provider_order 对应的数据字段。
    serper_api_key: str = ""  # serper_api_key 对应的数据字段。
    serper_base_url: str = "https://google.serper.dev/search"  # serper_base_url 对应的数据字段。
    exa_api_key: str = ""  # exa_api_key 对应的数据字段。
    exa_base_url: str = "https://api.exa.ai/search"  # exa_base_url 对应的数据字段。
    fallback_on_empty: bool = True  # fallback_on_empty 对应的数据字段。
    provider_timeout_seconds: float | None = None  # provider_timeout_seconds 对应的数据字段。

    @property
    def effective_provider_timeout_seconds(self) -> float:
        """处理 effective provider timeout seconds。"""
        return self.provider_timeout_seconds or self.timeout_seconds


@dataclass(frozen=True)
class TavilySearchRequest:
    """表示 处理 tavily search request 的后端数据结构或服务对象。"""

    task_id: str  # task_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    query: str  # query 对应的数据字段。
    max_results: int  # max_results 对应的数据字段。


@dataclass(frozen=True)
class NormalizedSearchSource:
    """表示 处理 normalized search source 的后端数据结构或服务对象。"""

    title: str  # title 对应的数据字段。
    url: str  # url 对应的数据字段。
    snippet: str  # snippet 对应的数据字段。
    provider_metadata: dict[str, Any]  # provider_metadata 对应的数据字段。

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

    query: str  # query 对应的数据字段。
    sources: list[NormalizedSearchSource]  # sources 对应的数据字段。

    def to_workflow_sources(self) -> list[dict[str, Any]]:
        """转换为目标格式 workflow sources。"""
        return [source.to_workflow_dict() for source in self.sources]


@dataclass(frozen=True)
class ProviderFailure:
    """表示 处理 provider failure 的后端数据结构或服务对象。"""

    provider: str  # provider 对应的数据字段。
    category: str  # category 对应的数据字段。
    message: str  # message 对应的数据字段。

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

    query: str  # query 对应的数据字段。
    sources: list[NormalizedSearchSource]  # sources 对应的数据字段。
    attempted_providers: tuple[str, ...]  # attempted_providers 对应的数据字段。
    selected_provider: str | None  # selected_provider 对应的数据字段。
    failures: tuple[ProviderFailure, ...]  # failures 对应的数据字段。
    fallback_reason: str | None = None  # fallback_reason 对应的数据字段。
