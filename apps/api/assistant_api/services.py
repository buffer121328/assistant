from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Task, TaskStatus
from .repositories import TaskCreate, TaskRepository


class TaskServiceError(ValueError):
    code = "task_service_error"
    status_code = 400


class UserNotFoundError(TaskServiceError):
    code = "user_not_found"
    status_code = 404


class TaskNotFoundError(TaskServiceError):
    code = "task_not_found"
    status_code = 404


class InvalidTaskStatusTransitionError(TaskServiceError):
    code = "invalid_task_status_transition"
    status_code = 409


VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
}


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = TaskRepository(session)

    async def create_task(
        self,
        *,
        user_id: str,
        platform: str,
        task_type: str,
        input_text: str,
        workflow_key: str | None = None,
        model_class: str | None = None,
        commit: bool = True,
    ) -> Task:
        if not await self.repository.user_exists(user_id):
            raise UserNotFoundError(f"User not found: {user_id}")

        task = await self.repository.create_task(
            TaskCreate(
                user_id=user_id,
                platform=platform,
                task_type=task_type,
                input_text=input_text,
                workflow_key=workflow_key,
                model_class=model_class,
            )
        )
        if commit:
            await self.session.commit()
            await self.session.refresh(task)
        return task

    async def get_task(self, task_id: str) -> Task:
        task = await self.repository.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task

    async def list_tasks(self, user_id: str) -> list[Task]:
        if not await self.repository.user_exists(user_id):
            raise UserNotFoundError(f"User not found: {user_id}")
        return await self.repository.list_tasks_by_user(user_id)

    async def update_status(self, task_id: str, status: TaskStatus) -> Task:
        task = await self.get_task(task_id)
        self._validate_transition(task, status)
        task.status = status.value
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def save_success(self, task_id: str, result_text: str) -> Task:
        task = await self.get_task(task_id)
        self._validate_transition(task, TaskStatus.SUCCESS)
        task.status = TaskStatus.SUCCESS.value
        task.result_text = result_text
        task.error_message = None
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def save_failure(self, task_id: str, error_message: str) -> Task:
        task = await self.get_task(task_id)
        self._validate_transition(task, TaskStatus.FAILED)
        task.status = TaskStatus.FAILED.value
        task.error_message = error_message
        await self.session.commit()
        await self.session.refresh(task)
        return task

    def _validate_transition(self, task: Task, next_status: TaskStatus) -> None:
        current_status = TaskStatus(task.status)
        if next_status not in VALID_TRANSITIONS.get(current_status, set()):
            raise InvalidTaskStatusTransitionError(
                "Invalid task status transition: "
                f"{current_status.value} -> {next_status.value}"
            )
