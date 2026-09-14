"""任务仓储：任务的创建与按用户维度的查询。"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.policies.enterprise import LOCAL_ORGANIZATION_ID, LOCAL_TENANT_ID
from domain.models import Task, TaskStatus, User


@dataclass(frozen=True)
class TaskCreate:
    """创建任务的输入数据。"""

    user_id: str  # 提交用户 ID
    platform: str  # 来源渠道（desktop/langbot 等）
    task_type: str  # 任务类型
    input_text: str  # 用户原始输入
    workflow_key: str | None = None  # 工作流标识，默认空
    model_class: str | None = None  # 指定模型类别，默认空
    conversation_id: str | None = None  # 关联会话 ID，默认空
    tenant_id: str = LOCAL_TENANT_ID  # 租户 ID，默认本地租户
    organization_id: str | None = LOCAL_ORGANIZATION_ID  # 组织 ID，默认本地根组织
    owner_type: str = "user"  # 归属主体类型，默认用户
    owner_id: str | None = None  # 归属主体 ID，空则回退 user_id
    visibility: str = "private"  # 可见性，默认私有
    requested_skill_names_json: str = "[]"


class TaskRepository:
    """任务仓储：围绕单个数据库会话提供任务写入与查询。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化仓储。

        Args:
            session: 异步数据库会话。
        """
        self.session = session

    async def user_exists(self, user_id: str) -> bool:
        """判断用户是否存在。

        Args:
            user_id: 用户 ID。
        """
        return await self.session.get(User, user_id) is not None

    async def get_user(self, user_id: str) -> User | None:
        """取出定义任务受信归属范围的持久化用户。

        Args:
            user_id: 用户 ID。
        """
        return await self.session.get(User, user_id)

    async def create_task(self, data: TaskCreate) -> Task:
        """创建一条 pending 状态的任务并 flush，返回 ORM 记录。

        Args:
            data: 任务创建数据。
        """
        task = Task(
            user_id=data.user_id,
            tenant_id=data.tenant_id,
            organization_id=data.organization_id,
            owner_type=data.owner_type,
            # owner 未显式指定时回退到提交用户本人
            owner_id=data.owner_id or data.user_id,
            visibility=data.visibility,
            platform=data.platform,
            task_type=data.task_type,
            input_text=data.input_text,
            status=TaskStatus.PENDING.value,
            workflow_key=data.workflow_key,
            model_class=data.model_class,
            conversation_id=data.conversation_id,
            requested_skill_names_json=data.requested_skill_names_json,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def get_task(self, task_id: str) -> Task | None:
        """按 ID 获取任务，不存在返回 None。

        Args:
            task_id: 任务 ID。
        """
        return await self.session.get(Task, task_id)

    async def get_task_by_user(self, *, task_id: str, user_id: str) -> Task | None:
        """按 ID + 用户双重条件获取任务（owner 隔离），不匹配返回 None。

        Args:
            task_id: 任务 ID。
            user_id: 所属用户 ID。
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
        """获取用户最近一条非 status 类型的任务（排除指定任务），无则返回 None。

        Args:
            user_id: 用户 ID。
            exclude_task_id: 需要排除的任务 ID（通常是当前 status 任务自身）。
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
        """列出用户全部任务，按创建时间倒序（过滤已归档会话的任务）。

        Args:
            user_id: 用户 ID。
        """
        from domain.models import Conversation
        from sqlalchemy import or_

        result = await self.session.scalars(
            select(Task)
            .outerjoin(Conversation, Task.conversation_id == Conversation.id)
            .where(
                Task.user_id == user_id,
                or_(Task.conversation_id.is_(None), Conversation.archived_at.is_(None)),
            )
            .order_by(Task.created_at.desc(), Task.id.desc())
        )
        return list(result)
