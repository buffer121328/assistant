from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import math
import re
from time import monotonic
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import (
    Memory,
    MemoryFeedback,
    MemoryLink,
    MemoryRetrievalTrace,
    MemoryRetrievalTraceItem,
    utc_now,
)
from .semantic import SemanticMemory
from .working_set import estimate_tokens

TimeIntent = Literal["current", "historical", "latest", "future", "expired"]
_TOKEN = re.compile(r"[\u3400-\u9fff]|[a-z0-9_]+", re.I)


@dataclass(frozen=True)
class RetrievalWeights:
    semantic: float = 0.30
    keyword: float = 0.15
    importance: float = 0.15
    recency: float = 0.10
    confirmation: float = 0.10
    feedback: float = 0.10
    scope: float = 0.05
    link_support: float = 0.05
    stale_penalty: float = 0.35
    harmful_penalty: float = 0.25
    recency_half_life_days: float = 30.0
    min_score: float = 0.30
    token_budget: int = 500
    max_items: int = 8

    def __post_init__(self) -> None:
        positive = (
            self.semantic
            + self.keyword
            + self.importance
            + self.recency
            + self.confirmation
            + self.feedback
            + self.scope
            + self.link_support
        )
        if not math.isclose(positive, 1.0, abs_tol=1e-6):
            raise ValueError("retrieval positive weights must sum to 1")
        if self.token_budget <= 0 or self.max_items <= 0:
            raise ValueError("retrieval budgets must be positive")


@dataclass(frozen=True)
class RetrievedMemory:
    memory_id: str
    content: str
    memory_type: str
    score: float
    historical: bool
    injected_tokens: int


@dataclass(frozen=True)
class RetrievalResult:
    items: tuple[RetrievedMemory, ...]
    trace_id: str
    mode: str
    time_intent: TimeIntent
    injected_tokens: int


def classify_time_intent(query: str) -> TimeIntent:
    value = query.lower()
    if any(word in value for word in ("当时", "历史", "之前", "过去")):
        return "historical"
    if any(word in value for word in ("最近一次", "最新", "上次")):
        return "latest"
    if any(word in value for word in ("未来", "计划", "接下来")):
        return "future"
    if any(word in value for word in ("已失效", "过期", "废弃")):
        return "expired"
    return "current"


async def retrieve_memories(
    *,
    session: AsyncSession,
    user_id: str,
    query: str,
    semantic_memory: SemanticMemory | None = None,
    weights: RetrievalWeights | None = None,
    now: datetime | None = None,
    task_id: str | None = None,
    conversation_id: str | None = None,
    scope_kind: str = "user/global",
    scope_id: str | None = None,
) -> RetrievalResult:
    config = weights or RetrievalWeights()
    current = now or utc_now()
    intent = classify_time_intent(query)
    started = monotonic()
    semantic_scores: dict[str, float] = {}
    mode = "keyword"
    if semantic_memory is not None and semantic_memory.enabled and query.strip():
        try:
            results = await semantic_memory.search(
                user_id=user_id,
                query=query.strip(),
                limit=max(config.max_items * 3, 10),
            )
            semantic_scores = {
                item.memory_id: max(0.0, min(float(item.score or 0.0), 1.0))
                for item in results
            }
            mode = "hybrid"
        except Exception:
            mode = "keyword_fallback"

    memories = list(
        await session.scalars(
            select(Memory).where(Memory.user_id == user_id, Memory.deleted_at.is_(None))
        )
    )
    feedback_rows = list(
        await session.scalars(
            select(MemoryFeedback).where(MemoryFeedback.user_id == user_id)
        )
    )
    feedback: dict[str, list[str]] = {}
    for row in feedback_rows:
        feedback.setdefault(row.memory_id, []).append(row.feedback_type)
    links = (
        list(
            await session.scalars(
                select(MemoryLink).where(
                    or_(
                        MemoryLink.source_memory_id.in_([item.id for item in memories]),
                        MemoryLink.target_memory_id.in_([item.id for item in memories]),
                    )
                )
            )
        )
        if memories
        else []
    )
    linked_ids = {link.source_memory_id for link in links} | {
        link.target_memory_id for link in links
    }
    query_tokens = set(_tokens(query))
    candidates: list[tuple[Memory, dict[str, float], float, str]] = []
    filtered: list[tuple[Memory, str]] = []
    for memory in memories:
        reason = _filter_reason(
            memory, intent=intent, now=current, scope_kind=scope_kind, scope_id=scope_id
        )
        if reason is not None:
            filtered.append((memory, reason))
            continue
        keyword = _keyword_score(
            query_tokens, set(_tokens(memory.normalized_content or memory.content))
        )
        semantic = semantic_scores.get(memory.id, 0.0)
        if keyword == 0 and semantic == 0:
            filtered.append((memory, "irrelevant"))
            continue
        age_days = max(
            0.0, (current - _aware(memory.updated_at)).total_seconds() / 86400
        )
        recency = math.exp(-math.log(2) * age_days / config.recency_half_life_days)
        signals = feedback.get(memory.id, [])
        helpful = signals.count("helpful")
        harmful = signals.count("harmful")
        components = {
            "semantic": semantic,
            "keyword": keyword,
            "importance": max(0.0, min(memory.importance_score / 10, 1.0)),
            "recency": recency,
            "confirmation": 1.0 if memory.confirmed_by_user else 0.0,
            "feedback": min(helpful / 3, 1.0),
            "scope": 1.0
            if memory.scope_kind == scope_kind and memory.scope_id == scope_id
            else 0.5,
            "link_support": 1.0 if memory.id in linked_ids else 0.0,
        }
        score = sum(components[name] * getattr(config, name) for name in components)
        score -= min(harmful, 1) * config.harmful_penalty
        if intent not in {"historical", "expired"} and (
            memory.status == "superseded" or memory.valid_to is not None
        ):
            score -= config.stale_penalty
        if score < config.min_score:
            filtered.append((memory, "below_threshold"))
            continue
        candidates.append((memory, components, score, "eligible"))

    candidates.sort(
        key=lambda item: (
            -int(
                intent in {"historical", "expired"}
                and (item[0].status == "superseded" or item[0].valid_to is not None)
            ),
            -item[2],
            -int(item[0].confirmed_by_user),
            item[0].id,
        )
    )
    selected: list[tuple[Memory, dict[str, float], float, int]] = []
    used = 0
    seen_supersedes: set[str] = set()
    for memory, components, score, _ in candidates:
        group = memory.supersedes_id or memory.id
        if group in seen_supersedes:
            filtered.append((memory, "contradiction_suppressed"))
            continue
        tokens = estimate_tokens(memory.content)
        if len(selected) >= config.max_items or used + tokens > config.token_budget:
            filtered.append((memory, "token_budget"))
            continue
        selected.append((memory, components, score, tokens))
        seen_supersedes.add(group)
        used += tokens

    trace = MemoryRetrievalTrace(
        user_id=user_id,
        task_id=task_id,
        conversation_id=conversation_id,
        query_hash=sha256(query.strip().encode("utf-8")).hexdigest(),
        retrieval_mode=mode,
        time_intent=intent,
        candidate_count=len(memories),
        injected_count=len(selected),
        injected_tokens=used,
        latency_ms=(monotonic() - started) * 1000,
    )
    session.add(trace)
    await session.flush()
    rank_by_id = {memory.id: rank for rank, (memory, _, _, _) in enumerate(selected, 1)}
    for memory, reason in filtered:
        session.add(
            MemoryRetrievalTraceItem(
                trace_id=trace.id,
                memory_id=memory.id,
                filter_reason=reason,
                component_scores_json="{}",
                final_score=0.0,
                final_rank=None,
                injected_tokens=0,
            )
        )
    for memory, components, score, tokens in selected:
        memory.access_count += 1
        memory.last_accessed_at = current
        session.add(
            MemoryRetrievalTraceItem(
                trace_id=trace.id,
                memory_id=memory.id,
                filter_reason="injected",
                component_scores_json=json.dumps(components, sort_keys=True),
                final_score=score,
                final_rank=rank_by_id[memory.id],
                injected_tokens=tokens,
            )
        )
    await session.flush()
    return RetrievalResult(
        items=tuple(
            RetrievedMemory(
                memory.id,
                memory.content,
                memory.memory_type,
                score,
                intent != "current",
                tokens,
            )
            for memory, _, score, tokens in selected
        ),
        trace_id=trace.id,
        mode=mode,
        time_intent=intent,
        injected_tokens=used,
    )


def _filter_reason(
    memory: Memory,
    *,
    intent: TimeIntent,
    now: datetime,
    scope_kind: str,
    scope_id: str | None,
) -> str | None:
    if memory.sensitivity in {"sensitive", "forbidden"}:
        return "sensitivity"
    if memory.status in {
        "candidate",
        "conflict_pending",
        "rejected",
        "archived",
        "deleted",
    }:
        return "status"
    if (
        memory.expires_at is not None
        and _aware(memory.expires_at) <= now
        and intent not in {"historical", "expired"}
    ):
        return "expired"
    if intent == "current" and (
        memory.status != "active" or memory.valid_to is not None
    ):
        return "not_current"
    if intent in {"historical", "expired"} and memory.status not in {
        "active",
        "superseded",
    }:
        return "not_historical"
    if memory.scope_kind not in {"user/global", scope_kind}:
        return "scope"
    if memory.scope_kind == scope_kind and memory.scope_id != scope_id:
        return "scope"
    return None


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(item.lower() for item in _TOKEN.findall(value))


def _keyword_score(query: set[str], content: set[str]) -> float:
    return 0.0 if not query or not content else len(query & content) / len(query)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
