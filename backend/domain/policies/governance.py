from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


GovernanceOutcome = Literal["allow", "deny", "require_approval"]


@dataclass(frozen=True)
class GovernanceDecision:
    """定义当前组件的职责和边界。"""

    outcome: GovernanceOutcome  # outcome 对应的数据字段。
    reason_code: str  # reason_code 对应的数据字段。
    tool_name: str  # tool_name 对应的数据字段。
    tool_snapshot_revision: int | None = None  # tool_snapshot_revision 对应的数据字段。
    tool_version: str | None = None  # tool_version 对应的数据字段。
    approval_fingerprint: str | None = None  # approval_fingerprint 对应的数据字段。

    def audit_payload(self) -> dict[str, object]:
        """执行当前组件定义的业务处理逻辑。"""
        payload: dict[str, object] = {
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "tool_name": self.tool_name,
        }
        if self.tool_snapshot_revision is not None:
            payload["tool_snapshot_revision"] = self.tool_snapshot_revision
        if self.tool_version is not None:
            payload["tool_version"] = self.tool_version
        if self.approval_fingerprint is not None:
            payload["approval_fingerprint"] = self.approval_fingerprint
        return payload


class GovernanceDecisionRecorder(Protocol):
    """定义当前组件的接口契约。"""

    async def record(
        self,
        *,
        task_id: str,
        user_id: str,
        decision: GovernanceDecision,
    ) -> None:
        """Record the final authorization decision without raw arguments.

        Args:
            task_id: 目标任务 ID。
            user_id: 目标用户 ID。
            decision: 当前操作的治理决策。
        """
        ...


class NoopGovernanceDecisionRecorder:
    """定义当前组件的职责和边界。"""

    async def record(
        self,
        *,
        task_id: str,
        user_id: str,
        decision: GovernanceDecision,
    ) -> None:
        """在未启用治理记录时忽略决策，保持调用方无需分支处理。

        Args:
            task_id: 目标任务 ID。
            user_id: 目标用户 ID。
            decision: 当前操作的治理决策。
        """
        return None


__all__ = [
    "GovernanceDecision",
    "GovernanceDecisionRecorder",
    "GovernanceOutcome",
    "NoopGovernanceDecisionRecorder",
]
