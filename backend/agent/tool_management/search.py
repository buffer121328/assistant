from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import json
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import ToolLog
from model_gateway import sanitize_text


SEARCH_WEB_TOOL_NAME = "search.web"
TOOL_STATUS_SUCCEEDED = "succeeded"
TOOL_STATUS_FAILED = "failed"
PROVIDER_TAVILY = "tavily"
PROVIDER_BRAVE = "brave"
PROVIDER_DUCKDUCKGO = "duckduckgo"
DEFAULT_SEARCH_PROVIDER_ORDER = (
    PROVIDER_TAVILY,
    PROVIDER_BRAVE,
    PROVIDER_DUCKDUCKGO,
)


class TavilyClientError(Exception):
    pass


class SearchProviderError(Exception):
    pass


class SearchProviderChainError(Exception):
    def __init__(
        self,
        message: str,
        *,
        attempted_providers: Sequence[str],
        failures: Sequence[ProviderFailure],
    ) -> None:
        super().__init__(message)
        self.attempted_providers = tuple(attempted_providers)
        self.failures = tuple(failures)


class SearchWebToolError(Exception):
    pass


@dataclass(frozen=True)
class TavilyConfig:
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
        return self.provider_timeout_seconds or self.timeout_seconds


@dataclass(frozen=True)
class TavilySearchRequest:
    task_id: str
    user_id: str
    query: str
    max_results: int


@dataclass(frozen=True)
class NormalizedSearchSource:
    title: str
    url: str
    snippet: str
    provider_metadata: dict[str, Any]

    def to_workflow_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider_metadata": self.provider_metadata,
        }


@dataclass(frozen=True)
class SearchWebResult:
    query: str
    sources: list[NormalizedSearchSource]

    def to_workflow_sources(self) -> list[dict[str, Any]]:
        return [source.to_workflow_dict() for source in self.sources]


@dataclass(frozen=True)
class ProviderFailure:
    provider: str
    category: str
    message: str

    def to_log_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "category": self.category,
            "message": _truncate(self.message, limit=300),
        }


@dataclass(frozen=True)
class SearchProviderChainResult:
    query: str
    sources: list[NormalizedSearchSource]
    attempted_providers: tuple[str, ...]
    selected_provider: str | None
    failures: tuple[ProviderFailure, ...]
    fallback_reason: str | None = None


class TavilyClientProtocol(Protocol):
    async def search(self, request: TavilySearchRequest) -> dict[str, Any]:
        pass


class SearchProvider(Protocol):
    name: str

    async def search(self, request: TavilySearchRequest) -> list[NormalizedSearchSource]:
        pass


class TavilyApiClient:
    def __init__(self, config: TavilyConfig) -> None:
        self.config = config

    async def search(self, request: TavilySearchRequest) -> dict[str, Any]:
        payload = {
            "api_key": self.config.api_key,
            "query": request.query,
            "max_results": request.max_results,
            "include_answer": False,
            "search_depth": "basic",
        }
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(
                    f"{self.config.base_url.rstrip('/')}/search",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise TavilyClientError("Tavily search request timed out") from exc
        except httpx.TransportError as exc:
            raise TavilyClientError(self._safe_error(exc)) from exc

        if response.status_code >= 400:
            raise TavilyClientError(self._safe_error(response.text))

        try:
            data = response.json()
        except ValueError as exc:
            raise TavilyClientError("Tavily search returned invalid JSON") from exc

        if not isinstance(data, dict):
            raise TavilyClientError("Tavily search returned invalid response shape")
        return data

    def _safe_error(self, value: object) -> str:
        return sanitize_text(value, extra_sensitive_values=[self.config.api_key])


class TavilySearchProvider:
    name = PROVIDER_TAVILY

    def __init__(
        self,
        *,
        client: TavilyClientProtocol,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        self.client = client
        self.sensitive_values = tuple(sensitive_values)

    async def search(self, request: TavilySearchRequest) -> list[NormalizedSearchSource]:
        payload = await self.client.search(request)
        return normalize_tavily_sources(
            payload,
            extra_sensitive_values=self.sensitive_values,
        )


class BraveSearchProvider:
    name = PROVIDER_BRAVE

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.sensitive_values = (api_key, *tuple(sensitive_values))

    async def search(self, request: TavilySearchRequest) -> list[NormalizedSearchSource]:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }
        params: dict[str, str | int] = {"q": request.query, "count": request.max_results}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(self.base_url, headers=headers, params=params)
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
        return sanitize_text(value, extra_sensitive_values=self.sensitive_values)


class DuckDuckGoSearchProvider:
    name = PROVIDER_DUCKDUCKGO

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.sensitive_values = tuple(sensitive_values)

    async def search(self, request: TavilySearchRequest) -> list[NormalizedSearchSource]:
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
            raise SearchProviderError("DuckDuckGo search returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SearchProviderError("DuckDuckGo search returned invalid response shape")
        return normalize_duckduckgo_sources(
            payload,
            extra_sensitive_values=self.sensitive_values,
            max_results=request.max_results,
        )

    def _safe_error(self, value: object) -> str:
        return sanitize_text(value, extra_sensitive_values=self.sensitive_values)


class SearchProviderChain:
    def __init__(
        self,
        providers: Sequence[SearchProvider],
        *,
        fallback_on_empty: bool,
        max_results: int,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        self.providers = tuple(providers)
        self.fallback_on_empty = fallback_on_empty
        self.max_results = max_results
        self.sensitive_values = tuple(sensitive_values)

    async def search(self, request: TavilySearchRequest) -> SearchProviderChainResult:
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
        safe_message = sanitize_text(exc, extra_sensitive_values=self.sensitive_values)
        if "traceback" in safe_message.lower():
            safe_message = "内部错误已脱敏"
        return ProviderFailure(
            provider=provider,
            category=_failure_category(exc),
            message=safe_message,
        )


class SearchWebTool:
    def __init__(
        self,
        *,
        session: AsyncSession,
        config: TavilyConfig,
        client: TavilyClientProtocol | None = None,
        provider_chain: SearchProviderChain | None = None,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        self.session = session
        self.config = config
        self.sensitive_values = tuple(sensitive_values)
        if provider_chain is None:
            tavily_client = client or TavilyApiClient(config)
            provider_chain = SearchProviderChain(
                [
                    TavilySearchProvider(
                        client=tavily_client,
                        sensitive_values=self._extra_sensitive_values(),
                    )
                ],
                fallback_on_empty=config.fallback_on_empty,
                max_results=config.max_results,
                sensitive_values=self._extra_sensitive_values(),
            )
        self.provider_chain = provider_chain

    async def search(
        self,
        *,
        task_id: str,
        user_id: str,
        query: str,
    ) -> SearchWebResult:
        request = TavilySearchRequest(
            task_id=task_id,
            user_id=user_id,
            query=query,
            max_results=self.config.max_results,
        )
        input_text = self._request_summary(request)

        try:
            chain_result = await self.provider_chain.search(request)
            result = SearchWebResult(query=query, sources=chain_result.sources)
        except SearchProviderChainError as exc:
            safe_error = self._safe_error(exc)
            await self._record_log(
                task_id=task_id,
                status=TOOL_STATUS_FAILED,
                input_text=input_text,
                output_text=None,
                error_message=self._error_summary(
                    safe_error,
                    attempted_providers=exc.attempted_providers,
                    failures=exc.failures,
                ),
            )
            raise SearchWebToolError(f"search.web failed: {safe_error}") from exc
        except Exception as exc:
            safe_error = self._safe_error(exc)
            await self._record_log(
                task_id=task_id,
                status=TOOL_STATUS_FAILED,
                input_text=input_text,
                output_text=None,
                error_message=self._error_summary(safe_error),
            )
            raise SearchWebToolError(f"search.web failed: {safe_error}") from exc

        await self._record_log(
            task_id=task_id,
            status=TOOL_STATUS_SUCCEEDED,
            input_text=input_text,
            output_text=self._response_summary(result, chain_result),
            error_message=None,
        )
        return result

    async def _record_log(
        self,
        *,
        task_id: str,
        status: str,
        input_text: str,
        output_text: str | None,
        error_message: str | None,
    ) -> None:
        self.session.add(
            ToolLog(
                task_id=task_id,
                tool_name=SEARCH_WEB_TOOL_NAME,
                status=status,
                input_text=input_text,
                output_text=output_text,
                error_message=error_message,
            )
        )
        await self.session.flush()

    def _request_summary(self, request: TavilySearchRequest) -> str:
        return self._safe_json(
            {
                "tool": SEARCH_WEB_TOOL_NAME,
                "task_id": request.task_id,
                "user_id": request.user_id,
                "query": _truncate(request.query),
                "max_results": request.max_results,
            }
        )

    def _response_summary(
        self,
        result: SearchWebResult,
        chain_result: SearchProviderChainResult,
    ) -> str:
        return self._safe_json(
            {
                "status": TOOL_STATUS_SUCCEEDED,
                "provider_chain": list(chain_result.attempted_providers),
                "provider": chain_result.selected_provider,
                "fallback_reason": chain_result.fallback_reason,
                "provider_failures": [
                    failure.to_log_dict() for failure in chain_result.failures
                ],
                "source_count": len(result.sources),
                "sources": [
                    source.to_workflow_dict()
                    for source in result.sources[: self.config.max_results]
                ],
            }
        )

    def _error_summary(
        self,
        error: str,
        *,
        attempted_providers: Sequence[str] = (),
        failures: Sequence[ProviderFailure] = (),
    ) -> str:
        return self._safe_json(
            {
                "status": TOOL_STATUS_FAILED,
                "provider_chain": list(attempted_providers),
                "provider_failures": [failure.to_log_dict() for failure in failures],
                "error": _truncate(error),
            }
        )

    def _safe_error(self, value: object) -> str:
        text = sanitize_text(
            value,
            extra_sensitive_values=self._extra_sensitive_values(),
        )
        if "traceback" in text.lower():
            return "内部错误已脱敏"
        return text

    def _safe_json(self, payload: dict[str, Any]) -> str:
        return sanitize_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ),
            extra_sensitive_values=self._extra_sensitive_values(),
        )

    def _extra_sensitive_values(self) -> tuple[str | None, ...]:
        return (
            self.config.api_key,
            self.config.brave_search_api_key,
            *self.sensitive_values,
        )


def build_tavily_config(settings: Any) -> TavilyConfig:
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


def normalize_tavily_sources(
    payload: dict[str, Any],
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> list[NormalizedSearchSource]:
    results = payload.get("results")
    if not isinstance(results, list):
        return []

    sources: list[NormalizedSearchSource] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue

        url = _safe_field(item.get("url"), extra_sensitive_values)
        title = _safe_field(item.get("title"), extra_sensitive_values)
        snippet = _safe_field(
            item.get("content") or item.get("snippet") or item.get("description"),
            extra_sensitive_values,
        )
        if not url or not title:
            continue
        if url == "[REDACTED]":
            continue

        sources.append(
            NormalizedSearchSource(
                title=title,
                url=url,
                snippet=snippet,
                provider_metadata=_provider_metadata(
                    item,
                    provider=PROVIDER_TAVILY,
                    source_rank=index,
                ),
            )
        )
    return _dedupe_sources(sources)


def normalize_brave_sources(
    payload: dict[str, Any],
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> list[NormalizedSearchSource]:
    web = payload.get("web")
    results = web.get("results") if isinstance(web, dict) else payload.get("results")
    if not isinstance(results, list):
        return []

    sources: list[NormalizedSearchSource] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        url = _safe_field(item.get("url"), extra_sensitive_values)
        title = _safe_field(item.get("title"), extra_sensitive_values)
        snippet = _safe_field(
            item.get("description") or item.get("snippet") or item.get("content"),
            extra_sensitive_values,
        )
        if not url or not title or url == "[REDACTED]":
            continue
        sources.append(
            NormalizedSearchSource(
                title=title,
                url=url,
                snippet=snippet,
                provider_metadata=_provider_metadata(
                    item,
                    provider=PROVIDER_BRAVE,
                    source_rank=index,
                ),
            )
        )
    return _dedupe_sources(sources)


def normalize_duckduckgo_sources(
    payload: dict[str, Any],
    *,
    extra_sensitive_values: Iterable[str | None] = (),
    max_results: int,
) -> list[NormalizedSearchSource]:
    candidates: list[dict[str, Any]] = []
    abstract_url = payload.get("AbstractURL")
    abstract_text = payload.get("AbstractText")
    heading = payload.get("Heading")
    if abstract_url and heading:
        candidates.append(
            {
                "title": heading,
                "url": abstract_url,
                "snippet": abstract_text,
            }
        )
    related_topics = payload.get("RelatedTopics")
    if isinstance(related_topics, list):
        candidates.extend(_flatten_duckduckgo_topics(related_topics))

    sources: list[NormalizedSearchSource] = []
    for index, item in enumerate(candidates, start=1):
        url = _safe_field(item.get("FirstURL") or item.get("url"), extra_sensitive_values)
        title = _safe_field(
            item.get("Text") or item.get("title") or item.get("Name"),
            extra_sensitive_values,
        )
        snippet = _safe_field(
            item.get("snippet") or item.get("Text") or item.get("Result"),
            extra_sensitive_values,
        )
        if not url or not title or url == "[REDACTED]":
            continue
        sources.append(
            NormalizedSearchSource(
                title=title,
                url=url,
                snippet=snippet,
                provider_metadata={
                    "provider": PROVIDER_DUCKDUCKGO,
                    "source_rank": index,
                },
            )
        )
        if len(sources) >= max_results:
            break
    return _dedupe_sources(sources)[:max_results]


def _flatten_duckduckgo_topics(items: Sequence[Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        nested = item.get("Topics")
        if isinstance(nested, list):
            flattened.extend(_flatten_duckduckgo_topics(nested))
        else:
            flattened.append(item)
    return flattened


def _provider_metadata(
    item: dict[str, Any],
    *,
    provider: str,
    source_rank: int,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"provider": provider, "source_rank": source_rank}
    for key in ("score", "published_date"):
        value = item.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def _dedupe_sources(sources: Sequence[NormalizedSearchSource]) -> list[NormalizedSearchSource]:
    deduped: list[NormalizedSearchSource] = []
    seen: set[str] = set()
    for source in sources:
        source_key = _source_key(source.url, source.title)
        if source_key in seen:
            continue
        seen.add(source_key)
        deduped.append(source)
    return deduped


def _source_key(url: str, title: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme and parts.netloc:
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))
    return title.strip().lower()


def _safe_field(
    value: object,
    extra_sensitive_values: Iterable[str | None],
) -> str:
    if value is None:
        return ""
    return sanitize_text(value, extra_sensitive_values=extra_sensitive_values).strip()


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return exc.__class__.__name__


def _truncate(value: str, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."
