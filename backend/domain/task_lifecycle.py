from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from model_gateway import sanitize_text

from domain.models import Approval, ApprovalStatus, ApprovalType, Task, TaskStatus, utc_now
from infrastructure.repositories import ApprovalRepository, TaskCreate, TaskRepository


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


class ApprovalNotFoundError(TaskServiceError):
    code = "approval_not_found"
    status_code = 404


class ApprovalDecisionConflictError(TaskServiceError):
    code = "approval_decision_conflict"
    status_code = 409


class InvalidCommandTaskError(TaskServiceError):
    code = "invalid_command_task"
    status_code = 400


VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.WAITING_APPROVAL,
    },
    TaskStatus.WAITING_APPROVAL: {TaskStatus.PENDING, TaskStatus.CANCELLED},
}

TERMINAL_TASK_STATUSES = {
    TaskStatus.SUCCESS.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}
DISPATCHABLE_TASK_STATUSES = TERMINAL_TASK_STATUSES | {
    TaskStatus.WAITING_APPROVAL.value
}


def _normalize_approval_requests(
    requests: Iterable[object],
) -> tuple[tuple[str, str, str, str | None], ...]:
    normalized: list[tuple[str, str, str, str | None]] = []
    for request in requests:
        if isinstance(request, Mapping):
            approval_type = request.get("approval_type")
            subject = request.get("subject")
            summary = request.get("summary")
            tool_name = request.get("tool_name")
        else:
            approval_type = getattr(request, "approval_type", None)
            subject = getattr(request, "subject", None)
            summary = getattr(request, "summary", None)
            tool_name = getattr(request, "tool_name", None)
        if not isinstance(approval_type, str) or approval_type not in {
            item.value for item in ApprovalType
        }:
            continue
        if not isinstance(subject, str) or not subject.strip():
            continue
        safe_subject = sanitize_text(subject).strip()[:128]
        safe_summary = sanitize_text(summary or "需要人工审批。").strip()[:1000]
        safe_tool_name = (
            sanitize_text(tool_name).strip()[:128]
            if isinstance(tool_name, str) and tool_name.strip()
            else None
        )
        item = (approval_type, safe_subject, safe_summary, safe_tool_name)
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


class TaskService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        success_hook: Callable[[Task], Awaitable[None]] | None = None,
    ) -> None:
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
        if not await self.repository.user_exists(user_id):
            raise UserNotFoundError(f"User not found: {user_id}")

        if conversation_id is not None:
            from domain.conversations import ConversationService

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
            from domain.conversations import ConversationService

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
        task = await self.repository.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task

    async def get_task_by_user(self, *, task_id: str, user_id: str) -> Task:
        task = await self.repository.get_task_by_user(task_id=task_id, user_id=user_id)
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
        normalized_requests = _normalize_approval_requests(approval_requests)
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
        if task.conversation_id is None:
            return
        from domain.conversations import ConversationService

        await ConversationService(self.session).append_message(
            conversation_id=task.conversation_id,
            user_id=task.user_id,
            role="assistant",
            content=content,
            task_id=task.id,
        )

    def _validate_transition(self, task: Task, next_status: TaskStatus) -> None:
        current_status = TaskStatus(task.status)
        if next_status not in VALID_TRANSITIONS.get(current_status, set()):
            raise InvalidTaskStatusTransitionError(
                "Invalid task status transition: "
                f"{current_status.value} -> {next_status.value}"
            )


@dataclass(frozen=True)
class ApprovalDecisionResult:
    approval: Approval
    task: Task
    changed: bool


class ApprovalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.task_repository = TaskRepository(session)
        self.repository = ApprovalRepository(session)

    async def list_for_owner(self, *, task_id: str, user_id: str) -> list[Approval]:
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
        task = await self.task_repository.get_task_by_user(
            task_id=task_id,
            user_id=user_id,
        )
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task
