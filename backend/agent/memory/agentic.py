from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

QueryType = Literal["keyword_only", "factual", "conceptual", "complex", "historical", "latest"]

_TOKEN = re.compile(r"[\u3400-\u9fff]|[a-z0-9_]+", re.I)


@dataclass(frozen=True)
class QueryTypeProfile:
    query_type: QueryType
    semantic: float
    keyword: float
    recency: float
    importance: float
    feedback: float


def classify_memory_query_type(query: str) -> QueryType:
    value = query.strip().lower()
    if any(word in value for word in ("当时", "历史", "之前", "过去", "曾经")):
        return "historical"
    if any(word in value for word in ("最近一次", "最新", "上次", "现在", "当前")):
        return "latest"
    tokens = _tokens(value)
    if any(mark in value for mark in (" and ", " or ", "以及", "同时", "并且", "?", "？")) or len(tokens) >= 8:
        return "complex"
    if len(tokens) <= 2 or any(ch in value for ch in ('"', "'", "#", ":", "-")):
        return "keyword_only"
    if any(word in value for word in ("为什么", "如何", "怎么", "概念", "解释", "关系")):
        return "conceptual"
    return "factual"


def query_type_profile(query_type: QueryType) -> QueryTypeProfile:
    profiles: dict[QueryType, QueryTypeProfile] = {
        "keyword_only": QueryTypeProfile(query_type, semantic=0.10, keyword=0.55, recency=0.10, importance=0.15, feedback=0.10),
        "factual": QueryTypeProfile(query_type, semantic=0.25, keyword=0.30, recency=0.15, importance=0.20, feedback=0.10),
        "conceptual": QueryTypeProfile(query_type, semantic=0.45, keyword=0.20, recency=0.10, importance=0.15, feedback=0.10),
        "complex": QueryTypeProfile(query_type, semantic=0.30, keyword=0.25, recency=0.15, importance=0.15, feedback=0.15),
        "historical": QueryTypeProfile(query_type, semantic=0.20, keyword=0.25, recency=0.05, importance=0.35, feedback=0.15),
        "latest": QueryTypeProfile(query_type, semantic=0.20, keyword=0.20, recency=0.40, importance=0.10, feedback=0.10),
    }
    return profiles[query_type]


def weighted_rrf(
    rankings: dict[str, list[str]],
    *,
    weights: dict[str, float],
    k: int = 60,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for signal, ranked_ids in rankings.items():
        weight = max(0.0, float(weights.get(signal, 0.0)))
        if weight == 0:
            continue
        seen: set[str] = set()
        for rank, item_id in enumerate(ranked_ids, 1):
            if item_id in seen:
                continue
            seen.add(item_id)
            scores[item_id] = scores.get(item_id, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def rrf_weights_for_query(query: str) -> dict[str, float]:
    profile = query_type_profile(classify_memory_query_type(query))
    return {
        "semantic": profile.semantic,
        "keyword": profile.keyword,
        "recency": profile.recency,
        "importance": profile.importance,
        "feedback": profile.feedback,
    }


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(match.group(0).lower() for match in _TOKEN.finditer(value))
