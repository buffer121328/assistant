from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.memory.retrieval import RetrievalWeights

from domain.models import (
    MemoryEffectiveness,
    MemoryEffectivenessEvent,
    MemoryReleaseReport,
    MemoryRetrievalPolicyVersion,
    User,
    utc_now,
)
from domain.services import MemoryService, TaskServiceError, UserNotFoundError


_VERSION = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
_ALLOWED_SCOPES = {"user/global", "user/project", "user/conversation", "agent/profile"}


class MemoryReleaseError(TaskServiceError):
    code = "memory_release_invalid"
    status_code = 400


@dataclass(frozen=True)
class ShadowComparison:
    active_version: str
    shadow_version: str
    active_memory_ids: tuple[str, ...]
    shadow_memory_ids: tuple[str, ...]
    changed_positions: int


class MemoryReleaseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.memories = MemoryService(session)

    async def record_effectiveness(
        self,
        *,
        user_id: str,
        memory_id: str,
        evidence_key: str,
        feedback_type: str = "none",
        outcome: str = "none",
    ) -> MemoryEffectiveness:
        if feedback_type not in {"helpful", "harmful", "none"}:
            raise MemoryReleaseError("invalid feedback type")
        if outcome not in {"success", "failure", "none"}:
            raise MemoryReleaseError("invalid outcome")
        if not evidence_key or len(evidence_key) > 128:
            raise MemoryReleaseError("invalid evidence key")
        await self.memories.get_memory(user_id=user_id, memory_id=memory_id)
        aggregate = await self.session.scalar(
            select(MemoryEffectiveness).where(
                MemoryEffectiveness.user_id == user_id,
                MemoryEffectiveness.memory_id == memory_id,
            )
        )
        if aggregate is None:
            aggregate = MemoryEffectiveness(user_id=user_id, memory_id=memory_id)
            self.session.add(aggregate)
            await self.session.flush()
        existing = await self.session.scalar(
            select(MemoryEffectivenessEvent.id).where(
                MemoryEffectivenessEvent.user_id == user_id,
                MemoryEffectivenessEvent.memory_id == memory_id,
                MemoryEffectivenessEvent.evidence_key == evidence_key,
            )
        )
        if existing is not None:
            return aggregate
        self.session.add(
            MemoryEffectivenessEvent(
                user_id=user_id,
                memory_id=memory_id,
                evidence_key=evidence_key,
                feedback_type=feedback_type,
                outcome=outcome,
            )
        )
        if feedback_type == "helpful":
            aggregate.helpful_count += 1
        elif feedback_type == "harmful":
            aggregate.harmful_count += 1
        if outcome == "success":
            aggregate.success_count += 1
        elif outcome == "failure":
            aggregate.failure_count += 1
        await self.session.flush()
        return aggregate

    async def bootstrap_active_policy(
        self,
        *,
        user_id: str,
        version: str,
        config: dict[str, object],
        scope_kind: str = "user/global",
        scope_id: str | None = None,
    ) -> MemoryRetrievalPolicyVersion:
        await self._require_user(user_id)
        scope_key = _scope_key(scope_kind, scope_id)
        active = await self.get_active_policy(user_id=user_id, scope_key=scope_key)
        if active is not None:
            return active
        item = MemoryRetrievalPolicyVersion(
            user_id=user_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            scope_key=scope_key,
            version=_version(version),
            status="active",
            config_json=_canonical_config(config),
            activated_at=utc_now(),
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def create_shadow_policy(
        self,
        *,
        user_id: str,
        version: str,
        config: dict[str, object],
        scope_kind: str = "user/global",
        scope_id: str | None = None,
    ) -> MemoryRetrievalPolicyVersion:
        await self._require_user(user_id)
        scope_key = _scope_key(scope_kind, scope_id)
        existing = await self.session.scalar(
            select(MemoryRetrievalPolicyVersion).where(
                MemoryRetrievalPolicyVersion.user_id == user_id,
                MemoryRetrievalPolicyVersion.scope_key == scope_key,
                MemoryRetrievalPolicyVersion.version == version,
            )
        )
        if existing is not None:
            return existing
        parent = await self.get_active_policy(user_id=user_id, scope_key=scope_key)
        item = MemoryRetrievalPolicyVersion(
            user_id=user_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            scope_key=scope_key,
            version=_version(version),
            status="shadow",
            config_json=_canonical_config(config),
            parent_version_id=parent.id if parent else None,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def persist_release_report(
        self,
        *,
        user_id: str,
        policy_version: str,
        report: dict[str, Any],
        scope_kind: str = "user/global",
        scope_id: str | None = None,
    ) -> MemoryReleaseReport:
        await self._require_user(user_id)
        scope_key = _scope_key(scope_kind, scope_id)
        if report.get("valid") is not True or report.get("version") != "v6-07":
            raise MemoryReleaseError("invalid release report")
        safe_payload = {
            "passed": bool(report.get("passed")),
            "automated_passed": bool(report.get("automated_passed")),
            "manual_evidence_complete": bool(report.get("manual_evidence_complete")),
            "gate_reasons": _string_list(report.get("gate_reasons")),
            "metrics": _metrics(report.get("metrics")),
            "case_ids": _string_list(report.get("case_ids")),
        }
        material = json.dumps(
            {
                "user_id": user_id,
                "scope_key": scope_key,
                "policy_version": _version(policy_version),
                **safe_payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        report_hash = sha256(material.encode()).hexdigest()
        existing = await self.session.scalar(
            select(MemoryReleaseReport).where(
                MemoryReleaseReport.report_hash == report_hash
            )
        )
        if existing is not None:
            return existing
        item = MemoryReleaseReport(
            user_id=user_id,
            scope_key=scope_key,
            policy_version=policy_version,
            report_hash=report_hash,
            passed=safe_payload["passed"],
            automated_passed=safe_payload["automated_passed"],
            manual_evidence_complete=safe_payload["manual_evidence_complete"],
            gate_reasons_json=json.dumps(safe_payload["gate_reasons"], sort_keys=True),
            metrics_json=json.dumps(safe_payload["metrics"], sort_keys=True),
            case_ids_json=json.dumps(safe_payload["case_ids"], sort_keys=True),
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def activate_policy(
        self,
        *,
        user_id: str,
        policy_id: str,
        report_id: str,
        approved: bool,
    ) -> MemoryRetrievalPolicyVersion:
        if not approved:
            raise MemoryReleaseError("policy activation requires approval")
        policy = await self._owned_policy(user_id=user_id, policy_id=policy_id)
        report = await self.session.scalar(
            select(MemoryReleaseReport).where(
                MemoryReleaseReport.id == report_id,
                MemoryReleaseReport.user_id == user_id,
            )
        )
        if (
            policy.status != "shadow"
            or report is None
            or not report.passed
            or report.scope_key != policy.scope_key
            or report.policy_version != policy.version
        ):
            raise MemoryReleaseError("policy activation report is invalid")
        current = await self.get_active_policy(
            user_id=user_id, scope_key=policy.scope_key
        )
        now = utc_now()
        if current is not None:
            current.status = "rolled_back"
            current.rolled_back_at = now
        policy.status = "active"
        policy.activated_at = now
        policy.activated_report_id = report.id
        await self.session.flush()
        return policy

    async def rollback_policy(
        self, *, user_id: str, policy_id: str
    ) -> MemoryRetrievalPolicyVersion:
        current = await self._owned_policy(user_id=user_id, policy_id=policy_id)
        if current.status != "active" or current.parent_version_id is None:
            raise MemoryReleaseError("active policy has no rollback parent")
        parent = await self._owned_policy(
            user_id=user_id, policy_id=current.parent_version_id
        )
        if parent.scope_key != current.scope_key:
            raise MemoryReleaseError("rollback parent scope mismatch")
        current.status = "rolled_back"
        current.rolled_back_at = utc_now()
        parent.status = "active"
        parent.rolled_back_at = None
        parent.activated_at = utc_now()
        await self.session.flush()
        return parent

    async def compare_shadow(
        self,
        *,
        user_id: str,
        shadow_policy_id: str,
        active_memory_ids: tuple[str, ...],
        shadow_memory_ids: tuple[str, ...],
    ) -> ShadowComparison:
        shadow = await self._owned_policy(
            user_id=user_id, policy_id=shadow_policy_id
        )
        if shadow.status != "shadow":
            raise MemoryReleaseError("policy is not shadow")
        active = await self.get_active_policy(
            user_id=user_id, scope_key=shadow.scope_key
        )
        if active is None:
            raise MemoryReleaseError("active policy is missing")
        length = max(len(active_memory_ids), len(shadow_memory_ids))
        changed = sum(
            (active_memory_ids[index] if index < len(active_memory_ids) else None)
            != (shadow_memory_ids[index] if index < len(shadow_memory_ids) else None)
            for index in range(length)
        )
        return ShadowComparison(
            active_version=active.version,
            shadow_version=shadow.version,
            active_memory_ids=active_memory_ids,
            shadow_memory_ids=shadow_memory_ids,
            changed_positions=changed,
        )

    async def get_active_policy(
        self, *, user_id: str, scope_key: str
    ) -> MemoryRetrievalPolicyVersion | None:
        return await self.session.scalar(
            select(MemoryRetrievalPolicyVersion).where(
                MemoryRetrievalPolicyVersion.user_id == user_id,
                MemoryRetrievalPolicyVersion.scope_key == scope_key,
                MemoryRetrievalPolicyVersion.status == "active",
            )
        )

    async def _owned_policy(
        self, *, user_id: str, policy_id: str
    ) -> MemoryRetrievalPolicyVersion:
        item = await self.session.scalar(
            select(MemoryRetrievalPolicyVersion).where(
                MemoryRetrievalPolicyVersion.id == policy_id,
                MemoryRetrievalPolicyVersion.user_id == user_id,
            )
        )
        if item is None:
            raise MemoryReleaseError("retrieval policy not found")
        return item

    async def _require_user(self, user_id: str) -> None:
        if await self.session.get(User, user_id) is None:
            raise UserNotFoundError("用户不存在")


async def load_active_retrieval_weights(
    *,
    session: AsyncSession,
    user_id: str,
    scope_kind: str = "user/global",
    scope_id: str | None = None,
    max_items_limit: int = 8,
) -> RetrievalWeights:
    scope_key = _scope_key(scope_kind, scope_id)
    item = await session.scalar(
        select(MemoryRetrievalPolicyVersion).where(
            MemoryRetrievalPolicyVersion.user_id == user_id,
            MemoryRetrievalPolicyVersion.scope_key == scope_key,
            MemoryRetrievalPolicyVersion.status == "active",
        )
    )
    if item is None:
        return RetrievalWeights(max_items=max_items_limit)
    try:
        config = json.loads(item.config_json)
        if not isinstance(config, dict):
            raise ValueError("policy config is not an object")
        config["max_items"] = min(int(config["max_items"]), max_items_limit)
        return RetrievalWeights(**cast(Any, config))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MemoryReleaseError("active retrieval policy config is invalid") from exc


def default_policy_config() -> dict[str, object]:
    return cast(dict[str, object], asdict(RetrievalWeights()))


def _canonical_config(config: dict[str, object]) -> str:
    defaults = default_policy_config()
    if set(config) != set(defaults):
        raise MemoryReleaseError("retrieval policy config fields are invalid")
    try:
        RetrievalWeights(**cast(Any, config))
    except (TypeError, ValueError) as exc:
        raise MemoryReleaseError("retrieval policy config is invalid") from exc
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


def _scope_key(scope_kind: str, scope_id: str | None) -> str:
    if scope_kind not in _ALLOWED_SCOPES:
        raise MemoryReleaseError("retrieval policy scope is invalid")
    if scope_kind == "user/global":
        if scope_id is not None:
            raise MemoryReleaseError("global policy cannot define scope id")
        return "user/global:"
    if not scope_id:
        raise MemoryReleaseError("scoped policy requires scope id")
    return f"{scope_kind}:{scope_id}"


def _version(value: str) -> str:
    if not _VERSION.fullmatch(value):
        raise MemoryReleaseError("retrieval policy version is invalid")
    return value


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise MemoryReleaseError("release report list is invalid")
    return list(value)


def _metrics(value: object) -> dict[str, int | float]:
    if not isinstance(value, dict):
        raise MemoryReleaseError("release report metrics are invalid")
    parsed: dict[str, int | float] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or isinstance(item, bool)
            or not isinstance(item, (int, float))
        ):
            raise MemoryReleaseError("release report metrics are invalid")
        parsed[key] = item
    return parsed
