from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.settings.config import Settings
from workers.monitoring import run_phase09_monitoring
from agent.governance.evolution import BehaviorEvolutionService
from memory.consolidation import run_memory_consolidation_maintenance
from memory.maintenance import maintain_memories
from memory.index_outbox import MemoryIndexOutboxConsumer
from memory.semantic import Mem0MemoryAdapter
from integrations.notifications import ReminderService, deliver_langbot_due
from channels.langbot.service import LangBotResultClient
from tools.builtin.schedule import AgentScheduleService
from domain.models import AgentScheduleRun, ToolLog
from tasks.events import TaskEventRepository


DispatchTask = Callable[[str], Awaitable[bool | None]]
MonitoringDispatchTask = Callable[[str], Awaitable[None]]


async def run_v2_maintenance(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    dispatch_task: DispatchTask | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """运行 v2 maintenance。

    Args:
        sessionmaker: sessionmaker 参数。
        settings: settings 参数。
        dispatch_task: dispatch_task 参数。
        now: now 参数。
    """
    result: dict[str, Any] = await run_phase09_monitoring(
        sessionmaker=sessionmaker,
        settings=settings,
        dispatch_task=_monitoring_dispatch(dispatch_task),
        now=now,
    )

    async with sessionmaker() as session:
        memory_result = await maintain_memories(session=session, now=now)
        consolidation_result = await run_memory_consolidation_maintenance(
            session=session,
            now=now or datetime.now().astimezone(),
        )
        suggestion = await BehaviorEvolutionService(session).evaluate(now=now)
        await session.commit()

    async with sessionmaker() as session:
        index_outbox_result = await MemoryIndexOutboxConsumer(
            session,
            semantic_memory=Mem0MemoryAdapter(settings.mem0_config_path),
        ).run_once(now=now)

    async with sessionmaker() as session:
        created_outbox_ids = await ReminderService(session).materialize_due(now=now)

    async with sessionmaker() as session:
        schedule_runs = await AgentScheduleService(session).materialize_due(now=now)

    (
        queued_schedule_run_ids,
        failed_schedule_run_ids,
    ) = await _dispatch_materialized_schedule_runs(
        sessionmaker=sessionmaker,
        settings=settings,
        schedule_runs=schedule_runs,
        dispatch_task=dispatch_task,
    )

    async with sessionmaker() as session:
        delivered_outbox_ids = await deliver_langbot_due(
            session=session,
            client=LangBotResultClient(settings),
            now=now,
        )

    result["archived_memory_ids"] = list(memory_result.archived_memory_ids)
    result["memory_consolidation"] = consolidation_result
    result["evolution_suggestion_created"] = suggestion is not None
    result["memory_index_outbox"] = {
        "recovered_count": index_outbox_result.recovered_count,
        "processed_count": index_outbox_result.processed_count,
        "succeeded_count": index_outbox_result.succeeded_count,
        "retry_count": index_outbox_result.retry_count,
        "failed_count": index_outbox_result.failed_count,
    }
    result["created_notification_outbox_ids"] = list(created_outbox_ids)
    result["materialized_schedule_run_ids"] = [run.id for run in schedule_runs]
    result["queued_schedule_run_ids"] = queued_schedule_run_ids
    result["failed_schedule_run_ids"] = failed_schedule_run_ids
    result["delivered_notification_outbox_ids"] = list(delivered_outbox_ids)
    return result


def _monitoring_dispatch(
    dispatch_task: DispatchTask | None,
) -> MonitoringDispatchTask | None:
    if dispatch_task is None:
        return None

    async def dispatch(task_id: str) -> None:
        await dispatch_task(task_id)

    return dispatch


async def _dispatch_materialized_schedule_runs(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    schedule_runs: list[AgentScheduleRun],
    dispatch_task: DispatchTask | None,
) -> tuple[list[str], list[str]]:
    """执行 分发 materialized schedule runs 的内部辅助逻辑。

    Args:
        sessionmaker: sessionmaker 参数。
        settings: settings 参数。
        schedule_runs: schedule_runs 参数。
        dispatch_task: dispatch_task 参数。
    """
    queued_run_ids: list[str] = []
    failed_run_ids: list[str] = []

    for materialized in schedule_runs:
        queued = False
        if materialized.task_id is not None:
            try:
                if dispatch_task is not None:
                    dispatch_result = await dispatch_task(materialized.task_id)
                    queued = dispatch_result is not False
                else:
                    from workers.worker import enqueue_task_execution

                    queued = enqueue_task_execution(
                        materialized.task_id,
                        runtime_settings=settings,
                    )
            except Exception:
                queued = False

        async with sessionmaker() as session:
            run = await session.get(AgentScheduleRun, materialized.id)
            if run is None:
                continue
            run.status = "queued" if queued else "enqueue_failed"
            run.message = None if queued else "Task queue unavailable."
            if run.task_id is not None:
                await TaskEventRepository(session).append(
                    task_id=run.task_id,
                    user_id=run.user_id,
                    event_type=(
                        "task.status.changed" if queued else "task.dispatch.failed"
                    ),
                    payload={
                        "source": "schedule",
                        "schedule_run_id": run.id,
                        "status": "pending",
                        "queued": queued,
                    },
                )
                session.add(
                    ToolLog(
                        task_id=run.task_id,
                        tool_name="schedule.dispatch",
                        status="succeeded" if queued else "failed",
                        output_text=(
                            '{"queued": true}' if queued else '{"queued": false}'
                        ),
                        error_message=None if queued else "Task queue unavailable.",
                    )
                )
            await session.commit()

        if queued:
            queued_run_ids.append(materialized.id)
        else:
            failed_run_ids.append(materialized.id)

    return queued_run_ids, failed_run_ids
