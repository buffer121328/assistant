from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import EvolutionChange, Task, TaskStatus, ToolLog, utc_now
from agent.governance.governed_evolution import GovernedEvolutionService, TargetKind


EVOLUTION_SUGGESTION_TOOL_NAME = "agent.governance.evolution.suggestion"


@dataclass(frozen=True)
class BehaviorMetrics:
    task_count: int
    successful_task_count: int
    failed_task_count: int
    waiting_approval_task_count: int
    tool_log_count: int
    failed_tool_log_count: int
    task_failure_rate: float
    approval_wait_rate: float


@dataclass(frozen=True)
class EvolutionSuggestion:
    target: str
    reason: str
    proposed_direction: str
    metrics: BehaviorMetrics


class BehaviorEvolutionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        evaluation_window_days: int = 7,
        task_failure_threshold: float = 0.5,
        approval_wait_threshold: float = 0.5,
    ) -> None:
        self.session = session
        self.evaluation_window_days = evaluation_window_days
        self.task_failure_threshold = task_failure_threshold
        self.approval_wait_threshold = approval_wait_threshold

    async def evaluate(
        self,
        *,
        now: datetime | None = None,
    ) -> EvolutionSuggestion | None:
        evaluated_at = now or utc_now()
        if await self._has_pending_suggestion(evaluated_at):
            return None

        metrics = await self.calculate_metrics(now=evaluated_at)
        suggestion = self._suggest(metrics)
        if suggestion is None:
            return None

        self.session.add(
            ToolLog(
                tool_name=EVOLUTION_SUGGESTION_TOOL_NAME,
                status=TaskStatus.WAITING_APPROVAL.value,
                input_text=self._safe_evidence(metrics),
                output_text=self._safe_suggestion(suggestion),
                created_at=evaluated_at,
            )
        )
        await self.session.flush()
        return suggestion

    async def evaluate_and_propose(
        self,
        *,
        governed: GovernedEvolutionService,
        task_id: str,
        user_id: str,
        target_kind: TargetKind,
        target_name: str,
        now: datetime | None = None,
    ) -> EvolutionChange | None:
        suggestion = await self.evaluate(now=now)
        if suggestion is None:
            return None
        return await governed.propose_append(
            task_id=task_id,
            user_id=user_id,
            target_kind=target_kind,
            target_name=target_name,
            guidance=suggestion.proposed_direction,
            evidence=self._safe_evidence(suggestion.metrics),
        )

    async def calculate_metrics(
        self,
        *,
        now: datetime | None = None,
    ) -> BehaviorMetrics:
        evaluated_at = now or utc_now()
        window_start = evaluated_at - timedelta(days=self.evaluation_window_days)
        tasks = list(
            await self.session.scalars(
                select(Task).where(
                    Task.created_at >= window_start,
                    Task.created_at <= evaluated_at,
                )
            )
        )
        tool_logs = list(
            await self.session.scalars(
                select(ToolLog).where(
                    ToolLog.created_at >= window_start,
                    ToolLog.created_at <= evaluated_at,
                )
            )
        )

        task_count = len(tasks)
        successful = sum(task.status == TaskStatus.SUCCESS.value for task in tasks)
        failed = sum(task.status == TaskStatus.FAILED.value for task in tasks)
        waiting = sum(
            task.status == TaskStatus.WAITING_APPROVAL.value for task in tasks
        )
        failed_logs = sum(log.status == "failed" for log in tool_logs)
        denominator = task_count or 1
        return BehaviorMetrics(
            task_count=task_count,
            successful_task_count=successful,
            failed_task_count=failed,
            waiting_approval_task_count=waiting,
            tool_log_count=len(tool_logs),
            failed_tool_log_count=failed_logs,
            task_failure_rate=failed / denominator,
            approval_wait_rate=waiting / denominator,
        )

    async def _has_pending_suggestion(self, evaluated_at: datetime) -> bool:
        day_start = datetime(
            evaluated_at.year,
            evaluated_at.month,
            evaluated_at.day,
            tzinfo=evaluated_at.tzinfo or UTC,
        )
        day_end = day_start + timedelta(days=1)
        existing = await self.session.scalar(
            select(ToolLog.id)
            .where(
                ToolLog.tool_name == EVOLUTION_SUGGESTION_TOOL_NAME,
                ToolLog.status == TaskStatus.WAITING_APPROVAL.value,
                ToolLog.created_at >= day_start,
                ToolLog.created_at < day_end,
            )
            .limit(1)
        )
        return existing is not None

    def _suggest(self, metrics: BehaviorMetrics) -> EvolutionSuggestion | None:
        if metrics.task_count == 0:
            return None
        if metrics.task_failure_rate >= self.task_failure_threshold:
            return EvolutionSuggestion(
                target="profile",
                reason="recent_task_failure_rate_threshold_met",
                proposed_direction="review planning constraints and tool guidance",
                metrics=metrics,
            )
        if metrics.approval_wait_rate >= self.approval_wait_threshold:
            return EvolutionSuggestion(
                target="skill",
                reason="recent_approval_wait_rate_threshold_met",
                proposed_direction="review approval guidance and task decomposition",
                metrics=metrics,
            )
        return None

    def _safe_evidence(self, metrics: BehaviorMetrics) -> str:
        return json.dumps(
            {
                "task_count": metrics.task_count,
                "successful_task_count": metrics.successful_task_count,
                "failed_task_count": metrics.failed_task_count,
                "waiting_approval_task_count": metrics.waiting_approval_task_count,
                "tool_log_count": metrics.tool_log_count,
                "failed_tool_log_count": metrics.failed_tool_log_count,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _safe_suggestion(self, suggestion: EvolutionSuggestion) -> str:
        return json.dumps(
            {
                "target": suggestion.target,
                "reason": suggestion.reason,
                "proposed_direction": suggestion.proposed_direction,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
