from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import Task
from assistant_api.repositories import ScheduledTaskRunRepository
from assistant_api.services import TaskService


DispatchTask = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ScheduledTaskDefinition:
    schedule_key: str
    user_id: str
    platform: str
    task_type: str
    input_text: str
    workflow_key: str | None = None
    model_class: str | None = None


class CronScheduler:
    def __init__(self, *, session: AsyncSession, dispatch_task: DispatchTask) -> None:
        self.session = session
        self.dispatch_task = dispatch_task
        self.run_repository = ScheduledTaskRunRepository(session)

    async def create_due_task(
        self,
        *,
        definition: ScheduledTaskDefinition,
        scheduled_for: datetime,
    ) -> Task:
        existing_run = await self.run_repository.get_by_slot(
            schedule_key=definition.schedule_key,
            scheduled_for=scheduled_for,
        )
        if existing_run is not None:
            existing_task = await self.session.get(Task, existing_run.task_id)
            if existing_task is None:
                raise RuntimeError("scheduled task run references a missing task")
            return existing_task

        task = await TaskService(self.session).create_task(
            user_id=definition.user_id,
            platform=definition.platform,
            task_type=definition.task_type,
            input_text=definition.input_text,
            workflow_key=definition.workflow_key,
            model_class=definition.model_class,
            commit=False,
        )
        await self.run_repository.create(
            schedule_key=definition.schedule_key,
            scheduled_for=scheduled_for,
            task_id=task.id,
        )
        await self.session.commit()
        await self.session.refresh(task)
        await self.dispatch_task(task.id)
        return task
