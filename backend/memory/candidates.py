from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


SourceTrust = Literal["trusted_user", "trusted_runtime", "untrusted_external"]


@dataclass(frozen=True)
class SourceEvent:
    """表示 处理 source event 的后端数据结构或服务对象。"""

    user_id: str
    source_kind: str
    source_id: str
    content: str
    trust: SourceTrust
    task_id: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True)
class CandidateDraft:
    """表示 处理 candidate draft 的后端数据结构或服务对象。"""

    memory_type: str
    atomic_content: str
    scope_kind: str
    scope_id: str | None
    confidence: float
    sensitivity: str
    source_spans: tuple[str, ...] = ()
    candidate_links: tuple[str, ...] = ()
    reason_code: str = "model_extracted"

    def validate(self) -> CandidateDraft:
        """校验。"""
        if self.memory_type not in {
            "profile",
            "fact",
            "preference",
            "episode",
            "procedure",
            "constraint",
            "working",
            "reflection",
        }:
            raise ValueError("candidate_memory_type_invalid")
        if self.scope_kind not in {
            "user/global",
            "user/project",
            "user/conversation",
            "agent/profile",
        }:
            raise ValueError("candidate_scope_invalid")
        if self.scope_kind != "user/global" and not self.scope_id:
            raise ValueError("candidate_scope_id_required")
        if not self.atomic_content.strip() or len(self.atomic_content) > 4_000:
            raise ValueError("candidate_content_invalid")
        if not 0 <= self.confidence <= 1:
            raise ValueError("candidate_confidence_invalid")
        if self.sensitivity not in {"public", "personal", "sensitive", "forbidden"}:
            raise ValueError("candidate_sensitivity_invalid")
        if not self.reason_code or len(self.reason_code) > 64:
            raise ValueError("candidate_reason_invalid")
        return self


class MemoryCandidateExtractor(Protocol):
    """表示 处理 memory candidate extractor 的后端数据结构或服务对象。"""

    async def extract(self, event: SourceEvent) -> CandidateDraft | None:
        """提取。

        Args:
            event: event 参数。
        """
        ...


class NoopMemoryCandidateExtractor:
    """表示 处理 noop memory candidate extractor 的后端数据结构或服务对象。"""

    async def extract(self, event: SourceEvent) -> CandidateDraft | None:
        """提取。

        Args:
            event: event 参数。
        """
        del event
        return None


def candidate_should_activate(
    *, event: SourceEvent, draft: CandidateDraft, allow_runtime_auto_activation: bool
) -> bool:
    """处理 candidate should activate。

    Args:
        event: event 参数。
        draft: draft 参数。
        allow_runtime_auto_activation: allow_runtime_auto_activation 参数。
    """
    if event.trust == "trusted_user" and event.source_kind in {
        "explicit_command",
        "gui_remember",
        "user_correction",
    }:
        return draft.sensitivity != "forbidden"
    return bool(
        allow_runtime_auto_activation
        and event.trust == "trusted_runtime"
        and draft.memory_type in {"fact", "episode"}
        and draft.sensitivity == "public"
        and draft.confidence >= 0.9
    )


def enforce_source_trust(event: SourceEvent, draft: CandidateDraft) -> CandidateDraft:
    """处理 enforce source trust。

    Args:
        event: event 参数。
        draft: draft 参数。
    """
    if event.trust != "untrusted_external":
        return draft
    return CandidateDraft(
        memory_type="episode",
        atomic_content=draft.atomic_content,
        scope_kind=draft.scope_kind,
        scope_id=draft.scope_id,
        confidence=draft.confidence,
        sensitivity=draft.sensitivity,
        source_spans=draft.source_spans,
        candidate_links=draft.candidate_links,
        reason_code="untrusted_external_evidence",
    )


def obvious_preference_conflict(existing: str, candidate: str) -> bool:
    """处理 obvious preference conflict。

    Args:
        existing: existing 参数。
        candidate: candidate 参数。
    """
    left = _preference_signature(existing)
    right = _preference_signature(candidate)
    return (
        left is not None
        and right is not None
        and left[0] == right[0]
        and left[1] != right[1]
    )


def _preference_signature(value: str) -> tuple[str, bool] | None:
    """执行 处理 preference signature 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    normalized = "".join(value.strip().split())
    for positive, negative in (("喜欢", "不喜欢"), ("使用", "不使用"), ("要", "不要")):
        if normalized.startswith(negative) and len(normalized) > len(negative):
            return normalized[len(negative) :], False
        if normalized.startswith(positive) and len(normalized) > len(positive):
            return normalized[len(positive) :], True
    return None


class StructuredCandidateClient(Protocol):
    """表示 处理 structured candidate client 的后端数据结构或服务对象。"""

    async def extract_candidate(self, payload: dict[str, object]) -> object:
        """提取 candidate。

        Args:
            payload: payload 参数。
        """
        ...


class FastPoolMemoryCandidateExtractor:
    """表示 处理 fast pool memory candidate extractor 的后端数据结构或服务对象。"""

    def __init__(self, client: StructuredCandidateClient) -> None:
        """初始化对象实例。

        Args:
            client: client 参数。
        """
        self.client = client

    async def extract(self, event: SourceEvent) -> CandidateDraft | None:
        """提取。

        Args:
            event: event 参数。
        """
        raw = await self.client.extract_candidate(
            {
                "pool": "fast",
                "source_kind": event.source_kind,
                "source_id": event.source_id,
                "trust": event.trust,
                "content": event.content[:20_000],
            }
        )
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("candidate_schema_invalid")
        required = {
            "memory_type",
            "atomic_content",
            "scope_kind",
            "confidence",
            "sensitivity",
            "reason_code",
        }
        if not required.issubset(raw):
            raise ValueError("candidate_schema_missing_fields")
        spans = raw.get("source_spans", [])
        links = raw.get("candidate_links", [])
        if not isinstance(spans, list) or not all(
            isinstance(item, str) for item in spans
        ):
            raise ValueError("candidate_source_spans_invalid")
        if not isinstance(links, list) or not all(
            isinstance(item, str) for item in links
        ):
            raise ValueError("candidate_links_invalid")
        confidence = raw["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            raise ValueError("candidate_confidence_invalid")
        return CandidateDraft(
            memory_type=_required_text(raw["memory_type"]),
            atomic_content=_required_text(raw["atomic_content"]),
            scope_kind=_required_text(raw["scope_kind"]),
            scope_id=(
                raw.get("scope_id") if isinstance(raw.get("scope_id"), str) else None
            ),
            confidence=float(confidence),
            sensitivity=_required_text(raw["sensitivity"]),
            source_spans=tuple(spans),
            candidate_links=tuple(links),
            reason_code=_required_text(raw["reason_code"]),
        ).validate()


def _required_text(value: object) -> str:
    """执行 处理 required text 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("candidate_text_invalid")
    return value.strip()
