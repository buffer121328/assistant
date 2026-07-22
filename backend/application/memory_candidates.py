from __future__ import annotations

from dataclasses import dataclass
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory import (
    classify_memory_sensitivity,
    memory_content_hash,
    normalize_memory_content,
)
from memory.candidates import (
    MemoryCandidateExtractor,
    SourceEvent,
    candidate_should_activate,
    enforce_source_trust,
    obvious_preference_conflict,
)

from domain.models import Memory, MemoryPolicy
from application.memory_service import MemoryService


@dataclass(frozen=True)
class CandidatePipelineResult:
    """表示 处理 candidate pipeline result 的后端数据结构或服务对象。"""

    status: str
    reason_code: str
    memory_id: str | None = None


class MemoryPolicyService:
    """表示 处理 memory policy service 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def set_never_remember(
        self,
        *,
        user_id: str,
        memory_type: str,
        scope_kind: str = "user/global",
        scope_id: str | None = None,
        enabled: bool = True,
    ) -> MemoryPolicy:
        """处理 set never remember。

        Args:
            user_id: user_id 参数。
            memory_type: memory_type 参数。
            scope_kind: scope_kind 参数。
            scope_id: scope_id 参数。
            enabled: enabled 参数。
        """
        key = f"never_remember:{memory_type}"
        item = await self.session.scalar(
            select(MemoryPolicy).where(
                MemoryPolicy.user_id == user_id,
                MemoryPolicy.policy_key == key,
                MemoryPolicy.scope_kind == scope_kind,
                MemoryPolicy.scope_id == scope_id,
            )
        )
        if item is None:
            item = MemoryPolicy(
                user_id=user_id,
                policy_key=key,
                scope_kind=scope_kind,
                scope_id=scope_id,
                value_json=json.dumps({"memory_type": memory_type}, sort_keys=True),
                enabled=enabled,
            )
            self.session.add(item)
        else:
            item.enabled = enabled
        await self.session.flush()
        return item

    async def blocks_candidate(
        self, *, user_id: str, memory_type: str, scope_kind: str, scope_id: str | None
    ) -> bool:
        """处理 blocks candidate。

        Args:
            user_id: user_id 参数。
            memory_type: memory_type 参数。
            scope_kind: scope_kind 参数。
            scope_id: scope_id 参数。
        """
        key = f"never_remember:{memory_type}"
        return (
            await self.session.scalar(
                select(MemoryPolicy.id).where(
                    MemoryPolicy.user_id == user_id,
                    MemoryPolicy.policy_key == key,
                    MemoryPolicy.enabled.is_(True),
                    MemoryPolicy.scope_kind == scope_kind,
                    MemoryPolicy.scope_id == scope_id,
                )
            )
            is not None
        )


class MemoryCandidatePipeline:
    """表示 处理 memory candidate pipeline 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        extractor: MemoryCandidateExtractor,
        allow_runtime_auto_activation: bool = False,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            extractor: extractor 参数。
            allow_runtime_auto_activation: allow_runtime_auto_activation 参数。
        """
        self.session = session
        self.extractor = extractor
        self.allow_runtime_auto_activation = allow_runtime_auto_activation
        self.memories = MemoryService(session)
        self.policies = MemoryPolicyService(session)

    async def process(self, event: SourceEvent) -> CandidatePipelineResult:
        """处理 process。

        Args:
            event: event 参数。
        """
        source_safety = classify_memory_sensitivity(event.content)
        if source_safety.sensitivity == "forbidden":
            return CandidatePipelineResult("rejected", "forbidden_source")
        try:
            extracted = await self.extractor.extract(event)
            if extracted is None:
                return CandidatePipelineResult("skipped", "no_candidate")
            draft = enforce_source_trust(event, extracted.validate()).validate()
        except Exception:
            return CandidatePipelineResult("failed", "extractor_failed")
        content = normalize_memory_content(draft.atomic_content)
        safety = classify_memory_sensitivity(content)
        if safety.sensitivity == "forbidden" or draft.sensitivity == "forbidden":
            return CandidatePipelineResult("rejected", "forbidden_candidate")
        if await self.policies.blocks_candidate(
            user_id=event.user_id,
            memory_type=draft.memory_type,
            scope_kind=draft.scope_kind,
            scope_id=draft.scope_id,
        ):
            return CandidatePipelineResult("skipped", "never_remember_policy")

        digest = memory_content_hash(content)
        duplicate = await self.session.scalar(
            select(Memory).where(
                Memory.user_id == event.user_id,
                Memory.content_hash == digest,
                Memory.deleted_at.is_(None),
            )
        )
        if duplicate is not None:
            return CandidatePipelineResult(
                "deduplicated", "content_hash_match", duplicate.id
            )

        active = list(
            await self.session.scalars(
                select(Memory).where(
                    Memory.user_id == event.user_id,
                    Memory.status == "active",
                    Memory.memory_type == draft.memory_type,
                    Memory.scope_kind == draft.scope_kind,
                    Memory.scope_id == draft.scope_id,
                )
            )
        )
        conflict = draft.memory_type in {"preference", "constraint"} and any(
            obvious_preference_conflict(item.normalized_content, content)
            for item in active
        )
        activate = candidate_should_activate(
            event=event,
            draft=draft,
            allow_runtime_auto_activation=self.allow_runtime_auto_activation,
        )
        memory = await self.memories.create_memory(
            user_id=event.user_id,
            content=content,
            memory_type=draft.memory_type,
            source_kind=event.source_kind,
            source_conversation_id=event.conversation_id,
            source_message_id=event.source_id,
            source_task_id=event.task_id,
            supersedes_id=active[0].id if conflict and active else None,
            confirmed_by_user=activate,
            source_trust=event.trust,
            source_spans_json=json.dumps(draft.source_spans, ensure_ascii=False),
            candidate_links_json=json.dumps(draft.candidate_links, ensure_ascii=False),
            reason_code=draft.reason_code,
        )
        memory.scope_kind = draft.scope_kind
        memory.scope_id = draft.scope_id
        memory.confidence = draft.confidence
        memory.sensitivity = safety.sensitivity
        if conflict:
            memory.status = "conflict_pending"
            memory.is_active = False
            memory.confirmed_by_user = False
            memory.confirmed_at = None
        await self.session.flush()
        return CandidatePipelineResult(
            "conflict" if conflict else ("active" if activate else "candidate"),
            "conflict_detected" if conflict else draft.reason_code,
            memory.id,
        )
