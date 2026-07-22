from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import Task, TaskStatus, User


@dataclass(frozen=True)
class TaskCreate:
    """表示 处理 task create 的后端数据结构或服务对象。"""

    user_id: str
    platform: str
    task_type: str
    input_text: str
    workflow_key: str | None = None
    model_class: str | None = None
    conversation_id: str | None = None


class TaskRepository:
    """表示 处理 task repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def user_exists(self, user_id: str) -> bool:
        """处理 user exists。

        Args:
            user_id: user_id 参数。
        """
        return await self.session.get(User, user_id) is not None

    async def create_task(self, data: TaskCreate) -> Task:
        """创建 task。

        Args:
            data: data 参数。
        """
        task = Task(
            user_id=data.user_id,
            platform=data.platform,
            task_type=data.task_type,
            input_text=data.input_text,
            status=TaskStatus.PENDING.value,
            workflow_key=data.workflow_key,
            model_class=data.model_class,
            conversation_id=data.conversation_id,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def get_task(self, task_id: str) -> Task | None:
        """获取 task。

        Args:
            task_id: task_id 参数。
        """
        return await self.session.get(Task, task_id)

    async def get_task_by_user(self, *, task_id: str, user_id: str) -> Task | None:
        """获取 task by user。

        Args:
            task_id: task_id 参数。
            user_id: user_id 参数。
        """
        return await self.session.scalar(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )

    async def get_latest_non_status_task(
        self,
        *,
        user_id: str,
        exclude_task_id: str,
    ) -> Task | None:
        """获取 latest non status task。

        Args:
            user_id: user_id 参数。
            exclude_task_id: exclude_task_id 参数。
        """
        return await self.session.scalar(
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.id != exclude_task_id,
                Task.task_type != "status",
            )
            .order_by(Task.created_at.desc(), Task.id.desc())
            .limit(1)
        )

    async def list_tasks_by_user(self, user_id: str) -> list[Task]:
        """列出 tasks by user。

        Args:
            user_id: user_id 参数。
        """
        result = await self.session.scalars(
            select(Task)
            .where(Task.user_id == user_id)
            .order_by(Task.created_at.desc(), Task.id.desc())
        )
        return list(result)
