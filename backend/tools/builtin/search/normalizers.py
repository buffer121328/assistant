from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from domain.policies.redaction import sanitize_text

from .constants import PROVIDER_EXA, PROVIDER_SERPER, PROVIDER_TAVILY
from .types import NormalizedSearchSource


def normalize_tavily_sources(
    payload: dict[str, Any],
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> list[NormalizedSearchSource]:
    """规范化 tavily sources。

    Args:
        payload: 当前操作的结构化载荷。
        extra_sensitive_values: 用于执行当前操作的 extra sensitive values 参数。
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
        if not url or not title or url == "[REDACTED]":
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


def normalize_serper_sources(
    payload: dict[str, Any],
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> list[NormalizedSearchSource]:
    """规范化 Serper organic 搜索结果。

    Args:
        payload: 当前操作的结构化载荷。
        extra_sensitive_values: 用于执行当前操作的 extra sensitive values 参数。
    """
    results = payload.get("organic") or payload.get("results")
    if not isinstance(results, list):
        return []

    sources: list[NormalizedSearchSource] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        url = _safe_field(item.get("link") or item.get("url"), extra_sensitive_values)
        title = _safe_field(item.get("title"), extra_sensitive_values)
        snippet = _safe_field(
            item.get("snippet") or item.get("description") or item.get("content"),
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
                    provider=PROVIDER_SERPER,
                    source_rank=index,
                ),
            )
        )
    return _dedupe_sources(sources)


def normalize_exa_sources(
    payload: dict[str, Any],
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> list[NormalizedSearchSource]:
    """规范化 Exa 搜索结果。

    Args:
        payload: 当前操作的结构化载荷。
        extra_sensitive_values: 用于执行当前操作的 extra sensitive values 参数。
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
            item.get("text")
            or item.get("snippet")
            or item.get("summary")
            or item.get("description"),
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
                    provider=PROVIDER_EXA,
                    source_rank=index,
                ),
            )
        )
    return _dedupe_sources(sources)


def _provider_metadata(
    item: dict[str, Any],
    *,
    provider: str,
    source_rank: int,
) -> dict[str, Any]:
    """执行 处理 provider metadata 的内部辅助逻辑。

    Args:
        item: 用于执行当前操作的 item 参数。
        provider: 用于执行当前操作的 provider 参数。
        source_rank: 用于执行当前操作的 source rank 参数。
    """
    metadata: dict[str, Any] = {"provider": provider, "source_rank": source_rank}
    for key in ("score", "published_date", "publishedDate", "date", "position"):
        value = item.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def _dedupe_sources(
    sources: Sequence[NormalizedSearchSource],
) -> list[NormalizedSearchSource]:
    """执行 处理 dedupe sources 的内部辅助逻辑。

    Args:
        sources: 用于执行当前操作的 sources 参数。
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
        url: 用于执行当前操作的 url 参数。
        title: 用于执行当前操作的 title 参数。
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
        value: 待校验、归一化或转换的输入值。
        extra_sensitive_values: 用于执行当前操作的 extra sensitive values 参数。
    """
    if value is None:
        return ""
    return sanitize_text(value, extra_sensitive_values=extra_sensitive_values).strip()


def _failure_category(exc: Exception) -> str:
    """执行 处理 failure category 的内部辅助逻辑。

    Args:
        exc: 用于执行当前操作的 exc 参数。
    """
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return exc.__class__.__name__
