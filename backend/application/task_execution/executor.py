from __future__ import annotations

from collections.abc import Awaitable, Callable
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.settings.config import Settings
from application.enterprise_governance import GovernedAgentProfileResolver
from application.session_context.context_snapshots import TaskContextSnapshotService
from application.session_context.execution_workspaces import ExecutionWorkspaceService

from application.task_execution.lifecycle import (
    InvalidTaskStatusTransitionError,
    TaskNotFoundError,
)
from application.task_execution.status import StatusService
from application.desktop_capture.service import ScreenTaskService
from domain.models import Task, TaskStatus
from domain.policies.enterprise import GovernanceValidationError
from memory import SemanticMemory
from application.user_memory import MemoryService

AgentTaskExecutor = Callable[[str], Awaitable[Task]]


class TaskExecutionService:
    """定义当前组件的职责和边界。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        agent_task_executor: AgentTaskExecutor,
        semantic_memory: SemanticMemory | None = None,
        settings: Settings | None = None,
        governed_profile_resolver: GovernedAgentProfileResolver | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            agent_task_executor: Agent task 执行回调。
            semantic_memory: semantic_memory 参数。
        """
        self.session = session
        self.agent_task_executor = agent_task_executor
        self.semantic_memory = semantic_memory
        self.settings = settings or Settings()
        self.governed_profile_resolver = (
            governed_profile_resolver or GovernedAgentProfileResolver(session)
        )

    async def execute(self, task_id: str) -> Task:
        """Execute a persisted task through the application use-case boundary.

        Args:
            task_id: 目标任务 ID。
        """
        return await self.execute_task(task_id)

    async def execute_task(self, task_id: str) -> Task:
        """执行 task。

        Args:
            task_id: task_id 参数。
        """
        task = await self._load_pending(task_id)
        workspace = ExecutionWorkspaceService(
            self.session,
            root=self.settings.session_workspace_root,
            uploaded_resources_root=self.settings.resource_uploads_root,
            artifacts_root=self.settings.artifacts_root,
        )
        await workspace.prepare(task)
        try:
            result = await self._execute_loaded_task(task)
        except Exception:
            await workspace.finalize(
                task,
                state=TaskStatus.FAILED.value,
                error_present=True,
            )
            raise
        await workspace.finalize(result)
        return result

    async def _execute_loaded_task(self, task: Task) -> Task:
        """Dispatch one prepared pending Task through its existing execution path.

        Args:
            task: 需要处理的任务对象。
        """
        if task.task_type == "memory":
            return await MemoryService(
                self.session,
                semantic_memory=self.semantic_memory,
            ).execute_task(task.id)
        if task.task_type == "status":
            return await StatusService(self.session).execute_task(task.id)
        if task.task_type == "screen":
            return await ScreenTaskService(
                self.session,
                settings=self.settings,
            ).execute_task(task.id)
        stable_task_id = task.id
        try:
            profile = await self.governed_profile_resolver.resolve_for_task(task)
            await TaskContextSnapshotService(self.session).finalize(
                task=task,
                profile=profile,
            )
            await self.session.commit()
        except GovernanceValidationError:
            await self.session.rollback()
            failed = await self.session.get(Task, stable_task_id)
            if failed is not None:
                failed.status = TaskStatus.FAILED.value
                failed.error_message = "Agent profile resolution failed"
                await self.session.commit()
            raise
        return await self.agent_task_executor(task.id)

    async def _load_pending(self, task_id: str) -> Task:
        """加载 pending task 供 application 层分流。

        Args:
            task_id: 目标任务 ID。
        """
        task = await self.session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        if task.status != TaskStatus.PENDING.value:
            raise InvalidTaskStatusTransitionError(
                f"Task is not pending: {task.id} ({task.status})"
            )
        return task


__all__ = ["TaskExecutionService"]
