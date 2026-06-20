from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Task, TaskStatus, User


@dataclass(frozen=True)
class TaskCreate:
    user_id: str
    platform: str
    task_type: str
    input_text: str
    workflow_key: str | None = None
    model_class: str | None = None


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def user_exists(self, user_id: str) -> bool:
        return await self.session.get(User, user_id) is not None

    async def create_task(self, data: TaskCreate) -> Task:
        task = Task(
            user_id=data.user_id,
            platform=data.platform,
            task_type=data.task_type,
            input_text=data.input_text,
            status=TaskStatus.PENDING.value,
            workflow_key=data.workflow_key,
            model_class=data.model_class,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def get_task(self, task_id: str) -> Task | None:
        return await self.session.get(Task, task_id)

    async def list_tasks_by_user(self, user_id: str) -> list[Task]:
        result = await self.session.scalars(
            select(Task)
            .where(Task.user_id == user_id)
            .order_by(Task.created_at.desc(), Task.id.desc())
        )
        return list(result)
