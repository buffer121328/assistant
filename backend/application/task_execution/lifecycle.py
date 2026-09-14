from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from application.lifecycle_hooks import (
    LifecycleHookRegistry,
    build_lifecycle_event,
    invoke_legacy_success_hook,
)

from domain.models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    OrganizationMembership,
    Task,
    TaskStatus,
    Tenant,
    utc_now,
)
from application.task_execution.events import TASK_EVENT_STATUS, TaskEventRepository
from domain.policies.approval_requests import normalize_approval_requests
from domain.policies.task_status import VALID_TRANSITIONS
from infrastructure.repositories import ApprovalRepository, TaskCreate, TaskRepository
from domain.policies.enterprise import GovernedAgentProfile, GovernanceValidationError
from domain.policies.enterprise import LOCAL_ORGANIZATION_ID, LOCAL_TENANT_ID

if TYPE_CHECKING:
    from application.session_context.resource_references import (
        ResolvedResourceReference,
    )


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
    _SAFE_CODES = frozenset(
        {
            "invalid_command_task",
            "command_required",
            "unknown_command",
            "command_unavailable",
            "command_task_type_mismatch",
        }
    )

    def __init__(self, code: str = "invalid_command_task") -> None:
        """初始化对象所需的运行时依赖和受限状态。

        Args:
            code: 可安全返回给调用方的错误码。
        """
        self.code = code if code in self._SAFE_CODES else "invalid_command_task"
        super().__init__(self.code)


class ConversationTokenBudgetExhaustedError(TaskServiceError):
    """Reject a continuation when its governed Conversation budget is exhausted."""

    code = "conversation_token_limit_exceeded"
    status_code = 409


def _subject_fingerprint(subject: str) -> str | None:
    """Extract an exact 64-character action fingerprint from governed subjects.

    Args:
        subject: 用于执行当前操作的 subject 参数。
    """
    _separator, marker, candidate = subject.rpartition(":")
    if not marker or len(candidate) != 64:
        return None
    return (
        candidate
        if all(character in "0123456789abcdef" for character in candidate)
        else None
    )


class TaskService:
    """表示 处理 task service 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        success_hook: Callable[[Task], Awaitable[None]] | None = None,
        uploaded_resources_root: Path = Path("var/resource-inputs"),
        artifacts_root: Path = Path("var/artifacts"),
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            success_hook: success_hook 参数。
        """
        self.session = session
        self.repository = TaskRepository(session)
        self.success_hook = success_hook
        self.uploaded_resources_root = uploaded_resources_root
        self.artifacts_root = artifacts_root

    async def create_task(
        self,
        *,
        user_id: str,
        platform: str,
        task_type: str,
        input_text: str,
        command_id: str | None = None,
        workflow_key: str | None = None,
        model_class: str | None = None,
        conversation_id: str | None = None,
        resource_reference_ids: tuple[str, ...] = (),
        selected_skill_names: tuple[str, ...] = (),
        commit: bool = True,
    ) -> Task:
        """创建 task。

        Args:
            user_id: user_id 参数。
            platform: platform 参数。
            task_type: task_type 参数。
            command_id: Structured server command ID, when selected.
            input_text: input_text 参数。
            workflow_key: workflow_key 参数。
            model_class: model_class 参数。
            conversation_id: conversation_id 参数。
            resource_reference_ids: Explicit Conversation resource IDs for this Task.
            commit: commit 参数。
        """
        user = await self.repository.get_user(user_id)
        if user is None:
            raise UserNotFoundError(f"User not found: {user_id}")

        from application.command_catalog import (
            CommandCatalogError,
            parse_task_type,
            resolve_office_command_for_user,
        )

        selected_command_id: str | None = None
        if command_id is not None:
            try:
                descriptor = await resolve_office_command_for_user(
                    self.session, user_id, command_id
                )
            except CommandCatalogError as exc:
                raise InvalidCommandTaskError(exc.code) from exc
            if task_type != descriptor.task_type:
                raise InvalidCommandTaskError("command_task_type_mismatch")
            selected_command_id = descriptor.command_id
        else:
            normalized_input = input_text.strip()
            if normalized_input.startswith("/"):
                parsed_task_type = parse_task_type(normalized_input)
                if parsed_task_type is None:
                    raise InvalidCommandTaskError("unknown_command")
                try:
                    descriptor = await resolve_office_command_for_user(
                        self.session, user_id, parsed_task_type
                    )
                except CommandCatalogError as exc:
                    raise InvalidCommandTaskError(exc.code) from exc
                if descriptor.task_type != task_type:
                    raise InvalidCommandTaskError("command_task_type_mismatch")
                selected_command_id = descriptor.command_id

        conversation = None
        if conversation_id is not None:
            from application.session_context.conversations import ConversationService
            from runtime.conversation_budget import ConversationTokenBudget

            conversation = await ConversationService(self.session).get_owned(
                conversation_id=conversation_id, user_id=user_id, active_only=True
            )
            budget = await ConversationTokenBudget(self.session).snapshot(
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if budget.status == "exhausted" or budget.remaining_tokens <= 0:
                raise ConversationTokenBudgetExhaustedError(
                    "Conversation model Token budget is exhausted."
                )

        tenant_id = user.tenant_id
        organization_id = (
            LOCAL_ORGANIZATION_ID if user.tenant_id == LOCAL_TENANT_ID else None
        )
        owner_type = "user"
        owner_id = user.id
        visibility = "private"
        if conversation is not None:
            tenant_id = conversation.tenant_id
            organization_id = conversation.organization_id
            owner_type = conversation.owner_type
            owner_id = conversation.owner_id
            visibility = conversation.visibility

        resolved_resources: tuple[ResolvedResourceReference, ...] = ()
        if resource_reference_ids:
            if conversation_id is None:
                from application.session_context.resource_references import (
                    ResourceReferenceError,
                )

                raise ResourceReferenceError("resource_conversation_required", 422)
            from application.session_context.resource_references import (
                ResourceReferenceService,
            )

            resolved_resources = await ResourceReferenceService(
                self.session,
                uploaded_resources_root=self.uploaded_resources_root,
                artifacts_root=self.artifacts_root,
            ).resolve_for_task(
                conversation_id=conversation_id,
                user_id=user_id,
                reference_ids=resource_reference_ids,
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
                tenant_id=tenant_id,
                organization_id=organization_id,
                owner_type=owner_type,
                owner_id=owner_id,
                visibility=visibility,
                requested_skill_names_json=json.dumps(
                    list(dict.fromkeys(selected_skill_names)), ensure_ascii=False
                ),
            )
        )
        from application.session_context.context_snapshots import (
            TaskContextSnapshotService,
        )

        await TaskContextSnapshotService(self.session).create_initial(
            task, command_id=selected_command_id
        )
        if resolved_resources:
            for resource in resolved_resources:
                if (
                    resource.conversation_id != task.conversation_id
                    or resource.user_id != task.user_id
                    or resource.tenant_id != task.tenant_id
                    or resource.organization_id != task.organization_id
                    or resource.owner_type != task.owner_type
                    or resource.owner_id != (task.owner_id or task.user_id)
                    or resource.visibility != task.visibility
                ):
                    from application.session_context.resource_references import (
                        ResourceReferenceError,
                    )

                    raise ResourceReferenceError("resource_reference_not_found", 404)
            await TaskContextSnapshotService(self.session).bind_resources(
                task=task,
                resource_versions=tuple(
                    (resource.id, resource.version) for resource in resolved_resources
                ),
            )
        if conversation_id is not None:
            from application.session_context.conversations import ConversationService

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
        task = await self._load_committed_task(task_id)
        self._validate_transition(task, status)
        task.status = status.value
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def cancel_task(self, task_id: str) -> Task:
        """处理 cancel task。

        Args:
            task_id: task_id 参数。
        """
        task = await self._load_committed_task(task_id)
        self._validate_transition(task, TaskStatus.CANCELLED)
        task.status = TaskStatus.CANCELLED.value
        task.result_text = "任务已被用户取消。"
        task.error_message = None
        await TaskEventRepository(self.session).append(
            task_id=task.id,
            user_id=task.user_id,
            event_type=TASK_EVENT_STATUS,
            payload={
                "status": TaskStatus.CANCELLED.value,
                "reason": "user_requested",
            },
        )
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def save_success(self, task_id: str, result_text: str) -> Task:
        """保存 success。

        Args:
            task_id: task_id 参数。
            result_text: result_text 参数。
        """
        task = await self._load_committed_task(task_id)
        self._validate_transition(task, TaskStatus.SUCCESS)
        task.status = TaskStatus.SUCCESS.value
        task.result_text = result_text
        task.error_message = None
        await self._append_assistant_message(task, result_text)
        await self.session.commit()
        await self.session.refresh(task)
        await invoke_legacy_success_hook(self.success_hook, task)
        return task

    async def save_failure(self, task_id: str, error_message: str) -> Task:
        """保存 failure。

        Args:
            task_id: task_id 参数。
            error_message: error_message 参数。
        """
        task = await self._load_committed_task(task_id)
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
        task = await self._load_committed_task(task_id)
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
                    request_fingerprint=_subject_fingerprint(subject),
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
        from application.session_context.conversations import ConversationService

        await ConversationService(self.session).append_message(
            conversation_id=task.conversation_id,
            user_id=task.user_id,
            role="assistant",
            content=content,
            task_id=task.id,
        )

    async def _load_committed_task(self, task_id: str) -> Task:
        """Reload committed status so transitions validate against the ledger, not a stale identity-map copy.

        Args:
            task_id: 目标任务 ID。
        """
        task = await self.get_task(task_id)
        await self.session.refresh(task, attribute_names=["status"])
        return task

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

    approval: Approval  # approval 对应的数据字段。
    task: Task  # task 对应的数据字段。
    changed: bool  # changed 对应的数据字段。


class ApprovalService:
    """表示 处理 approval service 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        lifecycle_registry: LifecycleHookRegistry | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session
        self.task_repository = TaskRepository(session)
        self.repository = ApprovalRepository(session)
        self.lifecycle_registry = lifecycle_registry

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

        task = await self.task_repository.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        approval = await self.repository.get_by_task(
            approval_id=approval_id,
            task_id=task_id,
        )
        if approval is None:
            raise ApprovalNotFoundError(f"Approval not found: {approval_id}")
        await self._require_decision_actor(
            task=task,
            approval=approval,
            user_id=user_id,
        )

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
        subject_fingerprint = _subject_fingerprint(approval.subject)
        if (
            approval.request_fingerprint is not None
            and subject_fingerprint is not None
            and subject_fingerprint != approval.request_fingerprint
        ):
            raise ApprovalDecisionConflictError("Approval request fingerprint mismatch")
        if approval.allowed_decisions_json and decision is ApprovalStatus.APPROVED:
            if "approve_once" not in approval.allowed_decisions_json:
                raise ApprovalDecisionConflictError("Approval choice is not allowed")
        if decision is ApprovalStatus.APPROVED and task.agent_profile_snapshot:
            try:
                profile = GovernedAgentProfile.from_snapshot(
                    task.agent_profile_snapshot
                )
            except GovernanceValidationError as exc:
                raise ApprovalDecisionConflictError(
                    "Approval profile is invalid"
                ) from exc
            tenant = await self.session.get(Tenant, task.tenant_id)
            if (
                tenant is None
                or tenant.authority_revision != profile.subject.authority_revision
            ):
                raise ApprovalDecisionConflictError("Approval authority changed")
            if (
                approval.approval_type == ApprovalType.TOOL.value
                and profile.capabilities
                and approval.tool_name not in profile.tools
            ):
                raise ApprovalDecisionConflictError(
                    "Approval capability is no longer available"
                )
        expiry = approval.expires_at
        if expiry is not None:
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= datetime.now(UTC):
                raise ApprovalDecisionConflictError("Approval expired")

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
        await self._dispatch_lifecycle_observer(task=task, approval=approval)
        return ApprovalDecisionResult(
            approval=approval,
            task=task,
            changed=True,
        )

    async def _dispatch_lifecycle_observer(
        self,
        *,
        task: Task,
        approval: Approval,
    ) -> None:
        """Publish one bounded approval resolution event after the decision commits.

        Args:
            task: 需要处理的任务对象。
            approval: 用于执行当前操作的 approval 参数。
        """
        if self.lifecycle_registry is None:
            return
        try:
            event = build_lifecycle_event(
                event_type="approval.resolved",
                actor_user_id=approval.decided_by_user_id or task.user_id,
                tenant_id=task.tenant_id,
                organization_id=task.organization_id,
                conversation_id=task.conversation_id,
                task_id=task.id,
                payload={
                    "approval_id": approval.id,
                    "approval_type": approval.approval_type,
                    "decision": approval.status,
                    "tool_name": approval.tool_name,
                },
            )
            await self.lifecycle_registry.dispatch_observers(event)
        except Exception:
            return

    async def _require_decision_actor(
        self,
        *,
        task: Task,
        approval: Approval,
        user_id: str,
    ) -> None:
        """Require the owner for confirmation or an independent scoped approver.

        Args:
            task: 需要处理的任务对象。
            approval: 用于执行当前操作的 approval 参数。
            user_id: 目标用户 ID。
        """
        if approval.policy_decision != "APPROVAL":
            if task.user_id != user_id:
                raise TaskNotFoundError(f"Task not found: {task.id}")
            return
        if task.user_id == user_id:
            raise ApprovalDecisionConflictError(
                "Approval requires an independent authorized approver"
            )
        roles = tuple(
            await self.session.scalars(
                select(OrganizationMembership.role).where(
                    OrganizationMembership.tenant_id == task.tenant_id,
                    OrganizationMembership.user_id == user_id,
                    OrganizationMembership.status == "active",
                    OrganizationMembership.role.in_(
                        ("department_admin", "enterprise_admin")
                    ),
                )
            )
        )
        scoped = "enterprise_admin" in roles
        if not scoped and task.organization_id is not None:
            scoped = (
                await self.session.scalar(
                    select(OrganizationMembership.id)
                    .where(
                        OrganizationMembership.tenant_id == task.tenant_id,
                        OrganizationMembership.organization_id == task.organization_id,
                        OrganizationMembership.user_id == user_id,
                        OrganizationMembership.role == "department_admin",
                        OrganizationMembership.status == "active",
                    )
                    .limit(1)
                )
                is not None
            )
        if not scoped:
            raise ApprovalDecisionConflictError(
                "Approval actor lacks current organization authority"
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
