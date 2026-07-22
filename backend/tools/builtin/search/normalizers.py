from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from domain.policies.redaction import sanitize_text

from .constants import PROVIDER_BRAVE, PROVIDER_DUCKDUCKGO, PROVIDER_TAVILY
from .types import NormalizedSearchSource


def normalize_tavily_sources(
    payload: dict[str, Any],
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> list[NormalizedSearchSource]:
    """规范化 tavily sources。

    Args:
        payload: payload 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
    """
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
    """规范化 brave sources。

    Args:
        payload: payload 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
    """
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
    """规范化 duckduckgo sources。

    Args:
        payload: payload 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
        max_results: max_results 参数。
    """
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
        url = _safe_field(
            item.get("FirstURL") or item.get("url"), extra_sensitive_values
        )
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
    """执行 处理 flatten duckduckgo topics 的内部辅助逻辑。

    Args:
        items: items 参数。
    """
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
    """执行 处理 provider metadata 的内部辅助逻辑。

    Args:
        item: item 参数。
        provider: provider 参数。
        source_rank: source_rank 参数。
    """
    metadata: dict[str, Any] = {"provider": provider, "source_rank": source_rank}
    for key in ("score", "published_date"):
        value = item.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def _dedupe_sources(
    sources: Sequence[NormalizedSearchSource],
) -> list[NormalizedSearchSource]:
    """执行 处理 dedupe sources 的内部辅助逻辑。

    Args:
        sources: sources 参数。
    """
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
    """执行 处理 source key 的内部辅助逻辑。

    Args:
        url: url 参数。
        title: title 参数。
    """
    parts = urlsplit(url.strip())
    if parts.scheme and parts.netloc:
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))
    return title.strip().lower()


def _safe_field(
    value: object,
    extra_sensitive_values: Iterable[str | None],
) -> str:
    """执行 处理 safe field 的内部辅助逻辑。

    Args:
        value: value 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
    """
    if value is None:
        return ""
    return sanitize_text(value, extra_sensitive_values=extra_sensitive_values).strip()


def _failure_category(exc: Exception) -> str:
    """执行 处理 failure category 的内部辅助逻辑。

    Args:
        exc: exc 参数。
    """
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return exc.__class__.__name__
