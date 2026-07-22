from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models import sanitize_text

from infrastructure.config import Settings
from domain.models import Approval, ApprovalStatus, Task, TaskEvent, TaskStatus, ToolLog
from infrastructure.repositories import ToolLogRepository
from domain.task_events import TaskEventRepository
from workers.worker import enqueue_task_execution


RUNNING_TIMEOUT_TOOL_NAME = "scheduler.running_timeout"
PENDING_COMPENSATION_TOOL_NAME = "scheduler.pending_compensation"
RECOVERY_DEAD_LETTER_EVENT = "task.recovery.dead_letter"
RECOVERY_WAITING_APPROVAL_EVENT = "task.recovery.waiting_approval"

DispatchTask = Callable[[str], Awaitable[None]]


async def fail_timed_out_running_tasks(
    *,
    session: AsyncSession,
    timeout_seconds: float,
    now: datetime | None = None,
    sensitive_values: Iterable[str | None] = (),
) -> list[str]:
    """处理 fail timed out running tasks。

    Args:
        session: session 参数。
        timeout_seconds: timeout_seconds 参数。
        now: now 参数。
        sensitive_values: sensitive_values 参数。
    """
    evaluated_at = now or datetime.now(UTC)
    cutoff = evaluated_at - timedelta(seconds=timeout_seconds)
    tasks = await session.scalars(
        select(Task)
        .where(
            Task.status == TaskStatus.RUNNING.value,
            Task.updated_at <= cutoff,
        )
        .order_by(Task.updated_at.asc(), Task.id.asc())
    )
    timed_out = list(tasks)
    if not timed_out:
        return []

    task_ids: list[str] = []
    for task in timed_out:
        summary = "任务执行超时，已由 phase 09 heartbeat 标记失败。"
        task.status = TaskStatus.FAILED.value
        task.result_text = None
        task.error_message = summary
        session.add(
            ToolLog(
                task_id=task.id,
                tool_name=RUNNING_TIMEOUT_TOOL_NAME,
                status="succeeded",
                input_text=_safe_json(
                    {
                        "timeout_seconds": timeout_seconds,
                        "evaluated_at": evaluated_at.isoformat(),
                        "previous_status": TaskStatus.RUNNING.value,
                    },
                    sensitive_values=sensitive_values,
                ),
                output_text=_safe_json(
                    {
                        "task_id": task.id,
                        "task_status": task.status,
                        "summary": summary,
                    },
                    sensitive_values=sensitive_values,
                ),
                error_message=None,
            )
        )
        await TaskEventRepository(session).append(
            task_id=task.id,
            user_id=task.user_id,
            event_type=RECOVERY_DEAD_LETTER_EVENT,
            payload={
                "recovery_status": "dead_letter",
                "retryable": False,
                "reason": "running_timeout",
                "timeout_seconds": timeout_seconds,
                "evaluated_at": evaluated_at.isoformat(),
                "tool_name": RUNNING_TIMEOUT_TOOL_NAME,
            },
        )
        task_ids.append(task.id)

    await session.commit()
    return task_ids


async def diagnose_waiting_approval_tasks(
    *,
    session: AsyncSession,
    now: datetime | None = None,
    sensitive_values: Iterable[str | None] = (),
) -> list[str]:
    """处理 diagnose waiting approval tasks。

    Args:
        session: session 参数。
        now: now 参数。
        sensitive_values: sensitive_values 参数。
    """
    evaluated_at = now or datetime.now(UTC)
    tasks = await session.scalars(
        select(Task)
        .where(Task.status == TaskStatus.WAITING_APPROVAL.value)
        .order_by(Task.updated_at.asc(), Task.id.asc())
    )
    diagnosed: list[str] = []
    for task in tasks:
        existing = await session.scalar(
            select(TaskEvent.id)
            .where(
                TaskEvent.task_id == task.id,
                TaskEvent.event_type == RECOVERY_WAITING_APPROVAL_EVENT,
            )
            .limit(1)
        )
        if existing is not None:
            continue
        pending_count = int(
            await session.scalar(
                select(func.count())
                .select_from(Approval)
                .where(
                    Approval.task_id == task.id,
                    Approval.status == ApprovalStatus.PENDING.value,
                )
            )
            or 0
        )
        if pending_count <= 0:
            continue
        await TaskEventRepository(session).append(
            task_id=task.id,
            user_id=task.user_id,
            event_type=RECOVERY_WAITING_APPROVAL_EVENT,
            payload={
                "recovery_status": "waiting_approval",
                "retryable": True,
                "reason": "approval_pending",
                "pending_approval_count": pending_count,
                "evaluated_at": evaluated_at.isoformat(),
            },
        )
        session.add(
            ToolLog(
                task_id=task.id,
                tool_name="scheduler.waiting_approval_recovery",
                status="succeeded",
                input_text=_safe_json(
                    {
                        "evaluated_at": evaluated_at.isoformat(),
                        "task_status": task.status,
                    },
                    sensitive_values=sensitive_values,
                ),
                output_text=_safe_json(
                    {
                        "recovery_status": "waiting_approval",
                        "pending_approval_count": pending_count,
                    },
                    sensitive_values=sensitive_values,
                ),
            )
        )
        diagnosed.append(task.id)
    await session.commit()
    return diagnosed


async def compensate_overdue_pending_tasks(
    *,
    session: AsyncSession,
    delay_seconds: float,
    dispatch_task: DispatchTask,
    now: datetime | None = None,
    sensitive_values: Iterable[str | None] = (),
) -> list[str]:
    """处理 compensate overdue pending tasks。

    Args:
        session: session 参数。
        delay_seconds: delay_seconds 参数。
        dispatch_task: dispatch_task 参数。
        now: now 参数。
        sensitive_values: sensitive_values 参数。
    """
    evaluated_at = now or datetime.now(UTC)
    cutoff = evaluated_at - timedelta(seconds=delay_seconds)
    tasks = await session.scalars(
        select(Task)
        .where(
            Task.status == TaskStatus.PENDING.value,
            Task.updated_at <= cutoff,
        )
        .order_by(Task.updated_at.asc(), Task.id.asc())
    )

    repository = ToolLogRepository(session)
    compensated: list[str] = []
    for task in tasks:
        if await repository.has_successful_tool_log(
            task_id=task.id,
            tool_name=PENDING_COMPENSATION_TOOL_NAME,
        ):
            continue

        try:
            await dispatch_task(task.id)
        except Exception as exc:
            session.add(
                ToolLog(
                    task_id=task.id,
                    tool_name=PENDING_COMPENSATION_TOOL_NAME,
                    status="failed",
                    input_text=_safe_json(
                        {
                            "delay_seconds": delay_seconds,
                            "evaluated_at": evaluated_at.isoformat(),
                            "task_status": task.status,
                        },
                        sensitive_values=sensitive_values,
                    ),
                    output_text=None,
                    error_message=_safe_text(exc, sensitive_values=sensitive_values),
                )
            )
            await session.commit()
            continue

        session.add(
            ToolLog(
                task_id=task.id,
                tool_name=PENDING_COMPENSATION_TOOL_NAME,
                status="succeeded",
                input_text=_safe_json(
                    {
                        "delay_seconds": delay_seconds,
                        "evaluated_at": evaluated_at.isoformat(),
                        "task_status": task.status,
                    },
                    sensitive_values=sensitive_values,
                ),
                output_text=_safe_json(
                    {
                        "task_id": task.id,
                        "action": "redispatch",
                    },
                    sensitive_values=sensitive_values,
                ),
                error_message=None,
            )
        )
        compensated.append(task.id)

    await session.commit()
    return compensated


async def run_phase09_monitoring(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    dispatch_task: DispatchTask | None = None,
    now: datetime | None = None,
) -> dict[str, list[str]]:
    """运行 phase09 monitoring。

    Args:
        sessionmaker: sessionmaker 参数。
        settings: settings 参数。
        dispatch_task: dispatch_task 参数。
        now: now 参数。
    """

    async def default_dispatch(task_id: str) -> None:
        """处理 default dispatch。

        Args:
            task_id: task_id 参数。
        """
        enqueue_task_execution(task_id, runtime_settings=settings)

    dispatch = dispatch_task or default_dispatch
    sensitive_values = (
        settings.redis_url,
        settings.langbot_api_key,
        settings.tavily_api_key,
        settings.deepseek_api_key,
    )

    async with sessionmaker() as session:
        timed_out = await fail_timed_out_running_tasks(
            session=session,
            timeout_seconds=settings.running_task_timeout_seconds,
            now=now,
            sensitive_values=sensitive_values,
        )

    async with sessionmaker() as session:
        await diagnose_waiting_approval_tasks(
            session=session,
            now=now,
            sensitive_values=sensitive_values,
        )

    async with sessionmaker() as session:
        compensated = await compensate_overdue_pending_tasks(
            session=session,
            delay_seconds=settings.pending_task_compensation_delay_seconds,
            dispatch_task=dispatch,
            now=now,
            sensitive_values=sensitive_values,
        )

    return {
        "timed_out_task_ids": timed_out,
        "compensated_task_ids": compensated,
    }


def _safe_text(
    value: object,
    *,
    sensitive_values: Iterable[str | None] = (),
) -> str:
    """执行 处理 safe text 的内部辅助逻辑。

    Args:
        value: value 参数。
        sensitive_values: sensitive_values 参数。
    """
    return sanitize_text(value, extra_sensitive_values=sensitive_values)


def _safe_json(
    payload: dict[str, object],
    *,
    sensitive_values: Iterable[str | None] = (),
) -> str:
    """执行 处理 safe json 的内部辅助逻辑。

    Args:
        payload: payload 参数。
        sensitive_values: sensitive_values 参数。
    """
    return _safe_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ),
        sensitive_values=sensitive_values,
    )
