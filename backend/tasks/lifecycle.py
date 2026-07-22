from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    Task,
    TaskStatus,
    utc_now,
)
from domain.policies.approval_requests import normalize_approval_requests
from domain.policies.task_status import VALID_TRANSITIONS
from infrastructure.repositories import ApprovalRepository, TaskCreate, TaskRepository


class TaskServiceError(ValueError):
    """表示 处理 task service error 的后端数据结构或服务对象。"""

    code = "task_service_error"
    status_code = 400


class UserNotFoundError(TaskServiceError):
    """表示 处理 user not found error 的后端数据结构或服务对象。"""

    code = "user_not_found"
    status_code = 404


class TaskNotFoundError(TaskServiceError):
    """表示 处理 task not found error 的后端数据结构或服务对象。"""

    code = "task_not_found"
    status_code = 404


class InvalidTaskStatusTransitionError(TaskServiceError):
    """表示 处理 invalid task status transition error 的后端数据结构或服务对象。"""

    code = "invalid_task_status_transition"
    status_code = 409


class ApprovalNotFoundError(TaskServiceError):
    """表示 处理 approval not found error 的后端数据结构或服务对象。"""

    code = "approval_not_found"
    status_code = 404


class ApprovalDecisionConflictError(TaskServiceError):
    """表示 处理 approval decision conflict error 的后端数据结构或服务对象。"""

    code = "approval_decision_conflict"
    status_code = 409


class InvalidCommandTaskError(TaskServiceError):
    """表示 处理 invalid command task error 的后端数据结构或服务对象。"""

    code = "invalid_command_task"
    status_code = 400


class TaskService:
    """表示 处理 task service 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        success_hook: Callable[[Task], Awaitable[None]] | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            success_hook: success_hook 参数。
        """
        self.session = session
        self.repository = TaskRepository(session)
        self.success_hook = success_hook

    async def create_task(
        self,
        *,
        user_id: str,
        platform: str,
        task_type: str,
        input_text: str,
        workflow_key: str | None = None,
        model_class: str | None = None,
        conversation_id: str | None = None,
        commit: bool = True,
    ) -> Task:
        """创建 task。

        Args:
            user_id: user_id 参数。
            platform: platform 参数。
            task_type: task_type 参数。
            input_text: input_text 参数。
            workflow_key: workflow_key 参数。
            model_class: model_class 参数。
            conversation_id: conversation_id 参数。
            commit: commit 参数。
        """
        if not await self.repository.user_exists(user_id):
            raise UserNotFoundError(f"User not found: {user_id}")

        if conversation_id is not None:
            from session.conversations import ConversationService

            await ConversationService(self.session).get_owned(
                conversation_id=conversation_id, user_id=user_id, active_only=True
            )

        task = await self.repository.create_task(
            TaskCreate(
                user_id=user_id,
                platform=platform,
                task_type=task_type,
                input_text=input_text,
                workflow_key=workflow_key,
                model_class=model_class,
                conversation_id=conversation_id,
            )
        )
        if conversation_id is not None:
            from session.conversations import ConversationService

            await ConversationService(self.session).append_message(
                conversation_id=conversation_id,
                user_id=user_id,
                role="user",
                content=input_text,
                task_id=task.id,
            )
        if commit:
            await self.session.commit()
            await self.session.refresh(task)
        return task

    async def get_task(self, task_id: str) -> Task:
        """获取 task。

        Args:
            task_id: task_id 参数。
        """
        task = await self.repository.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task

    async def get_task_by_user(self, *, task_id: str, user_id: str) -> Task:
        """获取 task by user。

        Args:
            task_id: task_id 参数。
            user_id: user_id 参数。
        """
        task = await self.repository.get_task_by_user(task_id=task_id, user_id=user_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task

    async def list_tasks(self, user_id: str) -> list[Task]:
        """列出 tasks。

        Args:
            user_id: user_id 参数。
        """
        if not await self.repository.user_exists(user_id):
            raise UserNotFoundError(f"User not found: {user_id}")
        return await self.repository.list_tasks_by_user(user_id)

    async def update_status(self, task_id: str, status: TaskStatus) -> Task:
        """更新 status。

        Args:
            task_id: task_id 参数。
            status: status 参数。
        """
        task = await self.get_task(task_id)
        self._validate_transition(task, status)
        task.status = status.value
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def save_success(self, task_id: str, result_text: str) -> Task:
        """保存 success。

        Args:
            task_id: task_id 参数。
            result_text: result_text 参数。
        """
        task = await self.get_task(task_id)
        self._validate_transition(task, TaskStatus.SUCCESS)
        task.status = TaskStatus.SUCCESS.value
        task.result_text = result_text
        task.error_message = None
        await self._append_assistant_message(task, result_text)
        await self.session.commit()
        await self.session.refresh(task)
        if self.success_hook is not None:
            try:
                await self.success_hook(task)
            except Exception:
                pass
        return task

    async def save_failure(self, task_id: str, error_message: str) -> Task:
        """保存 failure。

        Args:
            task_id: task_id 参数。
            error_message: error_message 参数。
        """
        task = await self.get_task(task_id)
        self._validate_transition(task, TaskStatus.FAILED)
        task.status = TaskStatus.FAILED.value
        task.result_text = None
        task.error_message = error_message
        await self._append_assistant_message(task, error_message)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def save_waiting_approval(
        self,
        task_id: str,
        message: str,
        *,
        requested_tools: Iterable[str] = (),
        approval_requests: Iterable[object] = (),
    ) -> Task:
        """保存 waiting approval。

        Args:
            task_id: task_id 参数。
            message: message 参数。
            requested_tools: requested_tools 参数。
            approval_requests: approval_requests 参数。
        """
        task = await self.get_task(task_id)
        self._validate_transition(task, TaskStatus.WAITING_APPROVAL)
        task.status = TaskStatus.WAITING_APPROVAL.value
        task.result_text = message
        task.error_message = None
        approval_repository = ApprovalRepository(self.session)
        normalized_tools = tuple(
            dict.fromkeys(tool.strip() for tool in requested_tools if tool.strip())
        )
        for tool_name in normalized_tools:
            existing = await approval_repository.get_active_for_tool(
                task_id=task.id,
                tool_name=tool_name,
            )
            if existing is None:
                await approval_repository.create_pending(
                    task_id=task.id,
                    tool_name=tool_name,
                )
        normalized_requests = normalize_approval_requests(approval_requests)
        for approval_type, subject, summary, requested_tool_name in normalized_requests:
            existing = await approval_repository.get_active_for_request(
                task_id=task.id,
                approval_type=approval_type,
                subject=subject,
            )
            if existing is None:
                tool_name = (
                    requested_tool_name or subject
                    if approval_type == ApprovalType.TOOL.value
                    else f"agent.{approval_type}"
                )
                await approval_repository.create_pending_request(
                    task_id=task.id,
                    approval_type=approval_type,
                    subject=subject,
                    tool_name=tool_name,
                    request_summary=summary,
                )
        await self._append_assistant_message(task, message)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def _append_assistant_message(self, task: Task, content: str) -> None:
        """执行 处理 append assistant message 的内部辅助逻辑。

        Args:
            task: task 参数。
            content: content 参数。
        """
        if task.conversation_id is None:
            return
        from session.conversations import ConversationService

        await ConversationService(self.session).append_message(
            conversation_id=task.conversation_id,
            user_id=task.user_id,
            role="assistant",
            content=content,
            task_id=task.id,
        )

    def _validate_transition(self, task: Task, next_status: TaskStatus) -> None:
        """执行 校验 transition 的内部辅助逻辑。

        Args:
            task: task 参数。
            next_status: next_status 参数。
        """
        current_status = TaskStatus(task.status)
        if next_status not in VALID_TRANSITIONS.get(current_status, set()):
            raise InvalidTaskStatusTransitionError(
                "Invalid task status transition: "
                f"{current_status.value} -> {next_status.value}"
            )


@dataclass(frozen=True)
class ApprovalDecisionResult:
    """表示 处理 approval decision result 的后端数据结构或服务对象。"""

    approval: Approval
    task: Task
    changed: bool


class ApprovalService:
    """表示 处理 approval service 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session
        self.task_repository = TaskRepository(session)
        self.repository = ApprovalRepository(session)

    async def list_for_owner(self, *, task_id: str, user_id: str) -> list[Approval]:
        """列出 for owner。

        Args:
            task_id: task_id 参数。
            user_id: user_id 参数。
        """
        await self._get_owned_task(task_id=task_id, user_id=user_id)
        return await self.repository.list_by_task(task_id)

    async def decide(
        self,
        *,
        task_id: str,
        approval_id: str,
        user_id: str,
        decision: ApprovalStatus,
    ) -> ApprovalDecisionResult:
        """处理 decide。

        Args:
            task_id: task_id 参数。
            approval_id: approval_id 参数。
            user_id: user_id 参数。
            decision: decision 参数。
        """
        if decision is ApprovalStatus.PENDING:
            raise ApprovalDecisionConflictError("Pending is not a decision")

        task = await self._get_owned_task(task_id=task_id, user_id=user_id)
        approval = await self.repository.get_by_task(
            approval_id=approval_id,
            task_id=task_id,
        )
        if approval is None:
            raise ApprovalNotFoundError(f"Approval not found: {approval_id}")

        if approval.status == decision.value:
            return ApprovalDecisionResult(
                approval=approval,
                task=task,
                changed=False,
            )
        if approval.status != ApprovalStatus.PENDING.value:
            raise ApprovalDecisionConflictError(
                f"Approval already decided: {approval.status}"
            )
        if task.status != TaskStatus.WAITING_APPROVAL.value:
            raise InvalidTaskStatusTransitionError(
                "Approval task is not waiting for a decision"
            )

        approval.status = decision.value
        approval.decided_by_user_id = user_id
        approval.decided_at = utc_now()
        if decision is ApprovalStatus.APPROVED:
            task.status = TaskStatus.PENDING.value
            task.result_text = None
            task.error_message = None
        else:
            task.status = TaskStatus.CANCELLED.value
            task.result_text = "任务审批已拒绝，不会继续执行。"
            task.error_message = None

        await self.session.commit()
        await self.session.refresh(approval)
        await self.session.refresh(task)
        return ApprovalDecisionResult(
            approval=approval,
            task=task,
            changed=True,
        )

    async def _get_owned_task(self, *, task_id: str, user_id: str) -> Task:
        """执行 获取 owned task 的内部辅助逻辑。

        Args:
            task_id: task_id 参数。
            user_id: user_id 参数。
        """
        task = await self.task_repository.get_task_by_user(
            task_id=task_id,
            user_id=user_id,
        )
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task
