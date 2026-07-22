from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tasks.commands import (
    _command_rest,
    _fail_task,
    _load_pending_task,
    _mark_running,
    _phase_label,
    _safe_summary,
    _succeed_task,
)
from tasks.lifecycle import TaskNotFoundError, TaskServiceError
from domain.models import Task
from infrastructure.repositories import TaskRepository


class StatusService:
    """表示 处理 status service 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session
        self.task_repository = TaskRepository(session)

    async def execute_task(self, task_id: str) -> Task:
        """执行 task。

        Args:
            task_id: task_id 参数。
        """
        task = await _load_pending_task(
            self.session,
            task_id,
            expected_task_type="status",
        )
        await _mark_running(self.session, task)

        try:
            target = await self._resolve_target(task)
            result_text = (
                "暂无可查询的任务状态。"
                if target is None
                else self._format_status_summary(target)
            )
        except TaskServiceError as exc:
            return await _fail_task(self.session, task, _safe_summary(exc))

        return await _succeed_task(self.session, task, result_text)

    async def _resolve_target(self, task: Task) -> Task | None:
        """执行 解析 target 的内部辅助逻辑。

        Args:
            task: task 参数。
        """
        rest = _command_rest(task.input_text, "/status")
        if not rest:
            return await self.task_repository.get_latest_non_status_task(
                user_id=task.user_id,
                exclude_task_id=task.id,
            )

        task_id = rest.split(maxsplit=1)[0]
        target = await self.task_repository.get_task_by_user(
            task_id=task_id,
            user_id=task.user_id,
        )
        if target is None:
            raise TaskNotFoundError("未找到可查询的任务或无权访问")
        return target

    def _format_status_summary(self, task: Task) -> str:
        """执行 处理 format status summary 的内部辅助逻辑。

        Args:
            task: task 参数。
        """
        lines = [
            "任务状态：",
            f"任务ID: {task.id}",
            f"类型: {task.task_type}",
            f"状态: {task.status}",
            f"创建时间: {task.created_at.isoformat()}",
            f"更新时间: {task.updated_at.isoformat()}",
            f"当前阶段: {_phase_label(task.status)}",
        ]
        if task.result_text:
            lines.append(f"结果摘要: {_safe_summary(task.result_text)}")
        if task.error_message:
            lines.append(f"错误摘要: {_safe_summary(task.error_message)}")
        return "\n".join(lines)
