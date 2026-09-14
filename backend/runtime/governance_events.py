from __future__ import annotations

from collections.abc import Awaitable, Callable

from domain.policies.governance import GovernanceDecision


TaskEventSink = Callable[[str, dict[str, object]], Awaitable[None]]


class EventSinkGovernanceDecisionRecorder:
    """定义当前组件的职责和边界。"""

    def __init__(self, event_sink: TaskEventSink) -> None:
        """保存事件接收器，用于将治理决策写入统一事件流。

        Args:
            event_sink: 用于执行当前操作的 event sink 参数。
        """
        self.event_sink = event_sink

    async def record(
        self,
        *,
        task_id: str,
        user_id: str,
        decision: GovernanceDecision,
    ) -> None:
        """将治理决策转换为安全事件载荷并异步写入事件接收器。

        Args:
            task_id: 目标任务 ID。
            user_id: 目标用户 ID。
            decision: 当前操作的治理决策。
        """
        del task_id, user_id
        await self.event_sink("governance.decision", decision.audit_payload())


__all__ = ["EventSinkGovernanceDecisionRecorder", "TaskEventSink"]
