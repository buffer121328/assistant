from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


_MAX_GATE_TEXT = 500


class ReadinessGateStatus(StrEnum):
    """定义当前组件的职责和边界。"""

    PASSED = "passed"
    FAILED = "failed"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class ReadinessGateEvidence:
    """定义当前组件的职责和边界。"""

    status: ReadinessGateStatus  # status 对应的数据字段。
    detail: str  # detail 对应的数据字段。

    def __post_init__(self) -> None:
        detail = self.detail.strip()
        if not detail:
            raise ValueError("Readiness gate evidence is required")
        if len(detail) > _MAX_GATE_TEXT:
            raise ValueError("Readiness gate evidence is too large")
        object.__setattr__(self, "detail", detail)


@dataclass(frozen=True)
class VNextReadinessReport:
    """定义当前组件的职责和边界。"""

    gates: Mapping[str, ReadinessGateEvidence]  # gates 对应的数据字段。

    @property
    def complete(self) -> bool:
        """执行当前组件定义的业务处理逻辑。"""
        return bool(self.gates) and all(
            evidence.status is ReadinessGateStatus.PASSED
            for evidence in self.gates.values()
        )


def evaluate_vnext_readiness(
    *,
    required_gates: tuple[str, ...],
    evidence: Mapping[str, ReadinessGateEvidence],
) -> VNextReadinessReport:
    """Build a fail-closed readiness report from the required gate names.

    Args:
        required_gates: 用于执行当前操作的 required gates 参数。
        evidence: 用于执行当前操作的 evidence 参数。
    """
    normalized = tuple(gate.strip() for gate in required_gates)
    if not normalized or any(not gate for gate in normalized):
        raise ValueError("At least one named readiness gate is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError("Readiness gate names must be unique")

    resolved: dict[str, ReadinessGateEvidence] = {}
    for gate in normalized:
        resolved[gate] = evidence.get(
            gate,
            ReadinessGateEvidence(
                ReadinessGateStatus.FAILED,
                "missing_current_evidence",
            ),
        )
    return VNextReadinessReport(gates=MappingProxyType(resolved))
