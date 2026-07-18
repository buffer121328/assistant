from __future__ import annotations

from collections.abc import Iterable
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


class TavilyClientError(Exception):
    pass


class SearchWebToolError(Exception):
    pass


@dataclass(frozen=True)
class TavilyConfig:
    base_url: str
    api_key: str
    timeout_seconds: float
    max_results: int


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


class TavilyClientProtocol(Protocol):
    async def search(self, request: TavilySearchRequest) -> dict[str, Any]:
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


class SearchWebTool:
    def __init__(
        self,
        *,
        client: TavilyClientProtocol,
        session: AsyncSession,
        config: TavilyConfig,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        self.client = client
        self.session = session
        self.config = config
        self.sensitive_values = tuple(sensitive_values)

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
            payload = await self.client.search(request)
            result = SearchWebResult(
                query=query,
                sources=normalize_tavily_sources(
                    payload,
                    extra_sensitive_values=self._extra_sensitive_values(),
                ),
            )
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
            output_text=self._response_summary(result),
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

    def _response_summary(self, result: SearchWebResult) -> str:
        return self._safe_json(
            {
                "status": TOOL_STATUS_SUCCEEDED,
                "source_count": len(result.sources),
                "sources": [
                    source.to_workflow_dict() for source in result.sources[: self.config.max_results]
                ],
            }
        )

    def _error_summary(self, error: str) -> str:
        return self._safe_json(
            {
                "status": TOOL_STATUS_FAILED,
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
        return (self.config.api_key, *self.sensitive_values)


def build_tavily_config(settings: Any) -> TavilyConfig:
    return TavilyConfig(
        base_url=settings.tavily_base_url,
        api_key=settings.tavily_api_key,
        timeout_seconds=settings.tavily_timeout_seconds,
        max_results=settings.tavily_max_results,
    )


def normalize_tavily_sources(
    payload: dict[str, Any],
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> list[NormalizedSearchSource]:
    results = payload.get("results")
    if not isinstance(results, list):
        return []

    sources: list[NormalizedSearchSource] = []
    seen: set[str] = set()
    for item in results:
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

        source_key = _source_key(url, title)
        if source_key in seen:
            continue
        seen.add(source_key)

        sources.append(
            NormalizedSearchSource(
                title=title,
                url=url,
                snippet=snippet,
                provider_metadata=_provider_metadata(item),
            )
        )
    return sources


def _provider_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"provider": "tavily"}
    for key in ("score", "published_date"):
        value = item.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


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


def _truncate(value: str, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."
