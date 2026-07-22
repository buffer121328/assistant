from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from agent.skill_management.lifecycle import SkillInventoryItem, SkillLifecycleService
from domain.models import ApprovalType, EvolutionChange, new_id
from infrastructure.repositories import ApprovalRepository
from domain.policies.redaction import sanitize_text


class SkillAcquisitionDecisionType(str, Enum):
    """表示 处理 skill acquisition decision type 的后端数据结构或服务对象。"""

    ENABLE_EXISTING = "enable_existing"
    INSTALL_MARKETPLACE = "install_marketplace"
    COMPOSE_EXISTING = "compose_existing"
    CREATE_CANDIDATE = "create_candidate"
    NO_SAFE_OPTION = "no_safe_option"


TRUST_SCORES: dict[str, float] = {
    "curated": 1.0,
    "workspace": 0.9,
    "personal": 0.7,
    "untrusted": 0.1,
}


@dataclass(frozen=True)
class SkillCandidate:
    """表示 处理 skill candidate 的后端数据结构或服务对象。"""

    skill_id: str
    name: str
    display_name: str
    summary: str
    version: str
    source: str
    trust_level: str
    capability_match: float
    dependency_risk: str = "low"
    permission_risk: str = "low"
    maintenance: float = 0.8
    testability: float = 0.8
    overlap: float = 0.0
    requires_approval: bool = True
    score: float = 0.0

    def scored(self) -> SkillCandidate:
        """处理 scored。"""
        permission_penalty = _risk_penalty(self.permission_risk)
        dependency_penalty = _risk_penalty(self.dependency_risk)
        trust = TRUST_SCORES.get(self.trust_level, 0.1)
        score = (
            self.capability_match * 0.38
            + trust * 0.20
            + self.maintenance * 0.12
            + self.testability * 0.10
            + (1 - dependency_penalty) * 0.08
            + (1 - permission_penalty) * 0.08
            + (1 - self.overlap) * 0.04
        )
        return SkillCandidate(**{**self.__dict__, "score": round(score, 4)})

    def to_dict(self) -> dict[str, object]:
        """转换为目标格式 dict。"""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "display_name": self.display_name,
            "summary": self.summary,
            "version": self.version,
            "source": self.source,
            "trust_level": self.trust_level,
            "score": self.score,
            "dependency_risk": self.dependency_risk,
            "permission_risk": self.permission_risk,
            "requires_approval": self.requires_approval,
        }


@dataclass(frozen=True)
class SkillAcquisitionDecision:
    """表示 处理 skill acquisition decision 的后端数据结构或服务对象。"""

    decision: SkillAcquisitionDecisionType
    candidates: tuple[SkillCandidate, ...] = ()
    local_skill: SkillInventoryItem | None = None
    risk_level: str = "L1"
    requires_approval: bool = False
    reason: str = ""
    composed_tools: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """转换为目标格式 dict。"""
        return {
            "decision": self.decision.value,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "local_skill": (
                {
                    "name": self.local_skill.name,
                    "display_name": self.local_skill.display_name,
                    "source": self.local_skill.source,
                    "enabled": self.local_skill.enabled,
                }
                if self.local_skill is not None
                else None
            ),
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "reason": self.reason,
            "composed_tools": list(self.composed_tools),
        }


@dataclass(frozen=True)
class SkillPackageMetadata:
    """表示 处理 skill package metadata 的后端数据结构或服务对象。"""

    candidate: SkillCandidate
    package_size: int
    checksum: str | None = None


class SkillMarketplaceProvider(Protocol):
    """表示 处理 skill marketplace provider 的后端数据结构或服务对象。"""

    name: str
    trust_level: str

    async def search(
        self,
        *,
        query: str,
        tags: Sequence[str] = (),
        capability_gap: str = "",
    ) -> Sequence[SkillCandidate]:
        """搜索。

        Args:
            query: query 参数。
            tags: tags 参数。
            capability_gap: capability_gap 参数。
        """
        ...

    async def get(
        self, *, skill_id: str, version: str | None = None
    ) -> SkillPackageMetadata:
        """获取。

        Args:
            skill_id: skill_id 参数。
            version: version 参数。
        """
        ...

    async def download(self, *, skill_id: str, version: str | None = None) -> bytes:
        """处理 download。

        Args:
            skill_id: skill_id 参数。
            version: version 参数。
        """
        ...


@dataclass
class SkillAcquisitionService:
    """表示 处理 skill acquisition service 的后端数据结构或服务对象。"""

    lifecycle: SkillLifecycleService
    marketplace_providers: Sequence[SkillMarketplaceProvider] = field(
        default_factory=tuple
    )
    installed_revision: int = 0

    async def recommend(
        self,
        *,
        capability_gap: str,
        query: str | None = None,
        tags: Sequence[str] = (),
        composable_tools: Sequence[str] = (),
        allow_create: bool = True,
    ) -> SkillAcquisitionDecision:
        """处理 recommend。

        Args:
            capability_gap: capability_gap 参数。
            query: query 参数。
            tags: tags 参数。
            composable_tools: composable_tools 参数。
            allow_create: allow_create 参数。
        """
        search_text = (query or capability_gap).strip()
        local = self._matching_local(search_text)
        enabled = [item for item in local if item.enabled]
        if enabled:
            item = enabled[0]
            return SkillAcquisitionDecision(
                decision=SkillAcquisitionDecisionType.ENABLE_EXISTING,
                local_skill=item,
                risk_level="L1",
                requires_approval=False,
                reason=f"Reuse enabled local Skill: {item.name}",
            )
        disabled = [item for item in local if not item.enabled]
        if disabled:
            item = disabled[0]
            return SkillAcquisitionDecision(
                decision=SkillAcquisitionDecisionType.ENABLE_EXISTING,
                local_skill=item,
                risk_level="L2",
                requires_approval=True,
                reason=f"Enable existing disabled local Skill: {item.name}",
            )

        candidates = await self.search_marketplace(
            query=search_text,
            tags=tags,
            capability_gap=capability_gap,
        )
        trusted = [
            item
            for item in candidates
            if item.score >= 0.75 and item.trust_level != "untrusted"
        ]
        if trusted:
            return SkillAcquisitionDecision(
                decision=SkillAcquisitionDecisionType.INSTALL_MARKETPLACE,
                candidates=tuple(trusted[:3]),
                risk_level=_candidate_risk_level(trusted[0]),
                requires_approval=True,
                reason="Trusted marketplace Skill candidate is preferred before creation.",
            )

        if composable_tools:
            return SkillAcquisitionDecision(
                decision=SkillAcquisitionDecisionType.COMPOSE_EXISTING,
                candidates=tuple(candidates[:3]),
                risk_level="L1",
                requires_approval=False,
                reason="No high-score marketplace Skill; compose existing capabilities.",
                composed_tools=tuple(composable_tools),
            )

        if allow_create:
            return SkillAcquisitionDecision(
                decision=SkillAcquisitionDecisionType.CREATE_CANDIDATE,
                candidates=tuple(candidates[:3]),
                risk_level="L2",
                requires_approval=True,
                reason="Local and marketplace options are insufficient; create a governed candidate package.",
            )

        return SkillAcquisitionDecision(
            decision=SkillAcquisitionDecisionType.NO_SAFE_OPTION,
            candidates=tuple(candidates[:3]),
            risk_level="L1",
            requires_approval=False,
            reason="No safe Skill acquisition option is available.",
        )

    async def search_marketplace(
        self,
        *,
        query: str,
        tags: Sequence[str] = (),
        capability_gap: str = "",
    ) -> tuple[SkillCandidate, ...]:
        """搜索 marketplace。

        Args:
            query: query 参数。
            tags: tags 参数。
            capability_gap: capability_gap 参数。
        """
        results: list[SkillCandidate] = []
        for provider in self.marketplace_providers:
            for candidate in await provider.search(
                query=query,
                tags=tags,
                capability_gap=capability_gap,
            ):
                trust_level = candidate.trust_level or provider.trust_level
                results.append(
                    SkillCandidate(
                        **{**candidate.__dict__, "trust_level": trust_level}
                    ).scored()
                )
        return tuple(
            sorted(
                results,
                key=lambda item: (TRUST_SCORES.get(item.trust_level, 0), item.score),
                reverse=True,
            )
        )

    async def install_candidate(
        self,
        *,
        user_id: str,
        candidate: SkillCandidate,
        package: bytes,
    ) -> SkillInventoryItem:
        """处理 install candidate。

        Args:
            user_id: user_id 参数。
            candidate: candidate 参数。
            package: package 参数。
        """
        item = await self.lifecycle.install(user_id=user_id, package=package)
        self.installed_revision += 1
        return item

    async def enable(self, *, user_id: str, name: str) -> SkillInventoryItem:
        """处理 enable。

        Args:
            user_id: user_id 参数。
            name: name 参数。
        """
        item = await self.lifecycle.set_enabled(
            user_id=user_id, name=name, enabled=True
        )
        self.installed_revision += 1
        return item

    async def disable(self, *, user_id: str, name: str) -> SkillInventoryItem:
        """处理 disable。

        Args:
            user_id: user_id 参数。
            name: name 参数。
        """
        item = await self.lifecycle.set_enabled(
            user_id=user_id, name=name, enabled=False
        )
        self.installed_revision += 1
        return item

    async def propose_create(
        self,
        *,
        session: AsyncSession,
        task_id: str,
        user_id: str,
        name: str,
        instructions: str,
        evidence: str,
    ) -> EvolutionChange:
        """处理 propose create。

        Args:
            session: session 参数。
            task_id: task_id 参数。
            user_id: user_id 参数。
            name: name 参数。
            instructions: instructions 参数。
            evidence: evidence 参数。
        """
        safe_name = sanitize_text(name).strip()[:128]
        safe_instructions = sanitize_text(instructions).strip()[:12000]
        safe_evidence = sanitize_text(evidence).strip()[:4000]
        change = EvolutionChange(
            task_id=task_id,
            user_id=user_id,
            target_kind="skill",
            target_name=safe_name,
            base_checksum="new-skill",
            candidate_checksum=new_id(),
            candidate_content=safe_instructions,
            evidence=safe_evidence,
            validation_result='{"status":"pending_validation"}',
            status="pending",
        )
        session.add(change)
        await session.flush()
        await ApprovalRepository(session).create_pending_request(
            task_id=task_id,
            approval_type=ApprovalType.CHANGE.value,
            subject=f"skill:{safe_name}",
            tool_name="skills.propose_create",
            request_summary="创建 Skill 候选包需要审批。",
        )
        return change

    def refresh_capabilities(self) -> dict[str, object]:
        """处理 refresh capabilities。"""
        self.lifecycle.refresh_registry()
        self.installed_revision += 1
        return {"revision": self.installed_revision, "status": "refreshed"}

    def _matching_local(self, query: str) -> list[SkillInventoryItem]:
        """执行 处理 matching local 的内部辅助逻辑。

        Args:
            query: query 参数。
        """
        needle = query.casefold().strip()
        if not needle:
            return []
        scored: list[tuple[int, SkillInventoryItem]] = []
        for item in self.lifecycle.list_skills():
            haystack = " ".join((item.name, item.display_name, item.summary)).casefold()
            if needle in haystack:
                scored.append((100, item))
                continue
            tokens = [
                token for token in needle.replace("-", " ").split() if len(token) > 2
            ]
            hits = sum(1 for token in tokens if token in haystack)
            if hits:
                scored.append((hits, item))
        return [
            item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)
        ]


def _risk_penalty(value: str) -> float:
    """执行 处理 risk penalty 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    return {"low": 0.0, "medium": 0.4, "high": 0.8}.get(value, 0.6)


def _candidate_risk_level(candidate: SkillCandidate) -> str:
    """执行 处理 candidate risk level 的内部辅助逻辑。

    Args:
        candidate: candidate 参数。
    """
    if candidate.permission_risk == "high" or candidate.dependency_risk == "high":
        return "L3"
    if candidate.permission_risk == "medium" or candidate.dependency_risk == "medium":
        return "L2"
    return "L2"
