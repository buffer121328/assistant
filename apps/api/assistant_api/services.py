from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from packages.model_gateway import sanitize_text
from packages.memory import NoopSemanticMemory, SemanticMemory

from .models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    Memory,
    ProcessedMessage,
    Task,
    TaskStatus,
    utc_now,
)
from .repositories import (
    ApprovalRepository,
    MemoryCreate,
    MemoryRepository,
    MessageRepository,
    TaskCreate,
    TaskRepository,
    ToolLogCreate,
    ToolLogRepository,
)


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


class MemoryNotFoundError(TaskServiceError):
    code = "memory_not_found"
    status_code = 404


class InvalidMemoryCommandError(TaskServiceError):
    code = "invalid_memory_command"
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
DISPATCHABLE_TASK_STATUSES = TERMINAL_TASK_STATUSES | {TaskStatus.WAITING_APPROVAL.value}


def _normalize_approval_requests(
    requests: Iterable[object],
) -> tuple[tuple[str, str, str], ...]:
    normalized: list[tuple[str, str, str]] = []
    for request in requests:
        if isinstance(request, Mapping):
            approval_type = request.get("approval_type")
            subject = request.get("subject")
            summary = request.get("summary")
        else:
            approval_type = getattr(request, "approval_type", None)
            subject = getattr(request, "subject", None)
            summary = getattr(request, "summary", None)
        if not isinstance(approval_type, str) or approval_type not in {
            item.value for item in ApprovalType
        }:
            continue
        if not isinstance(subject, str) or not subject.strip():
            continue
        safe_subject = sanitize_text(subject).strip()[:128]
        safe_summary = sanitize_text(summary or "需要人工审批。").strip()[:1000]
        item = (approval_type, safe_subject, safe_summary)
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


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
        task.result_text = None
        task.error_message = error_message
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
        for approval_type, subject, summary in normalized_requests:
            existing = await approval_repository.get_active_for_request(
                task_id=task.id,
                approval_type=approval_type,
                subject=subject,
            )
            if existing is None:
                tool_name = (
                    subject
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


class MemoryService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        semantic_memory: SemanticMemory | None = None,
    ) -> None:
        self.session = session
        self.repository = MemoryRepository(session)
        self.task_repository = TaskRepository(session)
        self.semantic_memory = semantic_memory or NoopSemanticMemory()

    async def create_memory(
        self,
        *,
        user_id: str,
        content: str,
        memory_type: str = "preference",
    ) -> Memory:
        normalized_content = content.strip()
        if not normalized_content:
            raise InvalidMemoryCommandError("记忆内容不能为空")
        return await self.repository.create_memory(
            MemoryCreate(
                user_id=user_id,
                content=normalized_content,
                memory_type=memory_type,
            )
        )

    async def list_active_memories(self, user_id: str) -> list[Memory]:
        return await self.repository.list_active_memories(user_id)

    async def delete_memory(self, *, user_id: str, memory_id: str) -> Memory:
        memory = await self.repository.get_active_memory_by_user(
            memory_id=memory_id,
            user_id=user_id,
        )
        if memory is None:
            raise MemoryNotFoundError("未找到可删除的记忆或无权访问")

        memory.is_active = False
        memory.deleted_at = utc_now()
        await self.session.flush()
        return memory

    async def execute_task(self, task_id: str) -> Task:
        task = await _load_pending_task(
            self.session,
            task_id,
            expected_task_type="memory",
        )
        await _mark_running(self.session, task)

        try:
            result_text = await self._execute_memory_command(task)
        except TaskServiceError as exc:
            return await _fail_task(self.session, task, _safe_summary(exc))

        return await _succeed_task(self.session, task, result_text)

    async def _execute_memory_command(self, task: Task) -> str:
        rest = _command_rest(task.input_text, "/memory")
        if rest.startswith("记住"):
            content = rest.removeprefix("记住").strip()
            memory = await self.create_memory(user_id=task.user_id, content=content)
            synced = await self._semantic_add(task=task, memory=memory)
            status = "语义记忆已同步" if synced else "语义记忆不可用，已保留 SQL 记录"
            return f"已保存记忆：{memory.id}；{status}"

        if rest == "查看":
            memories = await self.list_active_memories(task.user_id)
            if not memories:
                return "暂无记忆。"
            lines = ["当前记忆："]
            lines.extend(f"- {memory.id}: {memory.content}" for memory in memories)
            return "\n".join(lines)

        if rest.startswith("删除"):
            memory_id = rest.removeprefix("删除").strip()
            if not memory_id:
                raise InvalidMemoryCommandError("请提供要删除的 memory_id")
            await self.delete_memory(user_id=task.user_id, memory_id=memory_id)
            synced = await self._semantic_delete(
                user_id=task.user_id,
                memory_id=memory_id,
            )
            status = "语义记忆已同步" if synced else "语义记忆不可用，SQL 删除已生效"
            return f"已删除记忆：{memory_id}；{status}"

        raise InvalidMemoryCommandError(
            "不支持的 /memory 命令，请使用 /memory 记住、/memory 查看 或 /memory 删除"
        )

    async def _semantic_add(self, *, task: Task, memory: Memory) -> bool:
        if not self.semantic_memory.enabled:
            return False
        try:
            return await self.semantic_memory.add(
                user_id=task.user_id,
                run_id=task.id,
                memory_id=memory.id,
                content=memory.content,
            )
        except Exception:
            return False

    async def _semantic_delete(self, *, user_id: str, memory_id: str) -> bool:
        if not self.semantic_memory.enabled:
            return False
        try:
            return await self.semantic_memory.delete(
                user_id=user_id,
                memory_id=memory_id,
            )
        except Exception:
            return False


class StatusService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.task_repository = TaskRepository(session)

    async def execute_task(self, task_id: str) -> Task:
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


class LangBotMessageClientProtocol(Protocol):
    async def send_message(
        self,
        *,
        adapter: str,
        conversation_id: str,
        conversation_type: str,
        text: str,
    ) -> Any:
        pass


@dataclass(frozen=True)
class DispatchResult:
    status: str
    message: str


class ResultDispatcher:
    def __init__(
        self,
        session: AsyncSession,
        *,
        langbot_client: LangBotMessageClientProtocol | None = None,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        self.session = session
        self.langbot_client = langbot_client
        self.sensitive_values = tuple(sensitive_values)
        self.task_repository = TaskRepository(session)
        self.webhook_repository = MessageRepository(session)
        self.tool_log_repository = ToolLogRepository(session)

    async def dispatch_task(self, task_id: str) -> DispatchResult:
        task = await self.task_repository.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")

        if task.status not in DISPATCHABLE_TASK_STATUSES:
            return DispatchResult(status="skipped", message="任务尚未结束")

        if task.platform != "langbot":
            return DispatchResult(status="skipped", message="该来源不支持结果推送")

        tool_name = _dispatch_tool_name(task)
        already_dispatched = await self.tool_log_repository.has_successful_tool_log(
            task_id=task.id,
            tool_name=tool_name,
        )
        if already_dispatched:
            return DispatchResult(status="skipped", message="任务结果已推送")

        dispatch_record = await self.webhook_repository.get_task_dispatch_record(task.id)
        target = _resolve_dispatch_target(task=task, dispatch_record=dispatch_record)
        if target is None:
            message = _missing_target_message(task.platform)
            await self._record_dispatch(
                task=task,
                tool_name=tool_name,
                target_payload=None,
                status="failed",
                output_text=None,
                error_message=message,
            )
            await self.session.commit()
            return DispatchResult(status="failed", message=message)

        outbound_text = self._build_message(task)
        try:
            response = await self._send_message(
                task=task,
                target=target,
                outbound_text=outbound_text,
            )
        except Exception as exc:
            safe_error = _safe_summary(exc, extra_sensitive_values=self.sensitive_values)
            await self._record_dispatch(
                task=task,
                tool_name=tool_name,
                target_payload=target,
                status="failed",
                output_text=None,
                error_message=safe_error,
            )
            await self.session.commit()
            return DispatchResult(status="failed", message=safe_error)

        await self._record_dispatch(
            task=task,
            tool_name=tool_name,
            target_payload=target,
            status="succeeded",
            output_text=_safe_json(
                {"response": response},
                extra_sensitive_values=self.sensitive_values,
            ),
            error_message=None,
        )
        await self.session.commit()
        return DispatchResult(status="succeeded", message="任务结果已推送")

    def _build_message(self, task: Task) -> str:
        if task.status == TaskStatus.SUCCESS.value:
            title = "任务已完成"
            summary = task.result_text or "任务已完成。"
        elif task.status == TaskStatus.WAITING_APPROVAL.value:
            title = "任务等待审批"
            summary = (
                task.result_text
                or task.error_message
                or "任务需要人工批准后才能继续执行。"
            )
        elif task.status == TaskStatus.CANCELLED.value:
            title = "任务已取消"
            summary = task.result_text or "任务已取消。"
        else:
            title = "任务失败"
            summary = task.error_message or "任务执行失败。"

        return "\n".join(
            [
                title,
                f"任务ID: {task.id}",
                f"类型: {task.task_type}",
                f"摘要: {_safe_summary(summary, extra_sensitive_values=self.sensitive_values)}",
            ]
        )

    async def _send_message(
        self,
        *,
        task: Task,
        target: dict[str, str],
        outbound_text: str,
    ) -> Any:
        if task.platform == "langbot":
            if self.langbot_client is None:
                raise RuntimeError("LangBot client is not configured")
            return await self.langbot_client.send_message(
                adapter=target["adapter"],
                conversation_id=target["conversation_id"],
                conversation_type=target["conversation_type"],
                text=outbound_text,
            )

        raise RuntimeError(f"Unsupported dispatch platform: {task.platform}")

    async def _record_dispatch(
        self,
        *,
        task: Task,
        tool_name: str,
        target_payload: dict[str, str] | None,
        status: str,
        output_text: str | None,
        error_message: str | None,
    ) -> None:
        await self.tool_log_repository.create_tool_log(
            ToolLogCreate(
                task_id=task.id,
                tool_name=tool_name,
                status=status,
                input_text=_safe_json(
                    {
                        "platform": task.platform,
                        "target": target_payload,
                        "task_id": task.id,
                        "task_status": task.status,
                    },
                    extra_sensitive_values=self.sensitive_values,
                ),
                output_text=output_text,
                error_message=(
                    None
                    if error_message is None
                    else _safe_summary(
                        error_message,
                        extra_sensitive_values=self.sensitive_values,
                    )
                ),
            )
        )


def _dispatch_tool_name(task: Task) -> str:
    if task.platform == "langbot" and task.status == TaskStatus.WAITING_APPROVAL.value:
        return "langbot.approval_dispatch"
    if task.platform == "langbot":
        return "langbot.result_dispatch"
    raise ValueError(f"Unsupported dispatch platform: {task.platform}")


def _missing_target_message(platform: str) -> str:
    return {
        "langbot": "缺少 LangBot 推送目标",
    }.get(platform, "缺少推送目标")


def _resolve_dispatch_target(
    *,
    task: Task,
    dispatch_record: ProcessedMessage | None,
) -> dict[str, str] | None:
    if dispatch_record is None:
        return None

    if task.platform != "langbot" or not dispatch_record.response_target:
        return None

    try:
        payload = json.loads(dispatch_record.response_target)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    adapter = payload.get("adapter")
    conversation_id = payload.get("conversation_id")
    conversation_type = payload.get("conversation_type")
    if (
        not isinstance(adapter, str)
        or not adapter
        or not isinstance(conversation_id, str)
        or not conversation_id
        or not isinstance(conversation_type, str)
        or not conversation_type
    ):
        return None
    return {
        "adapter": adapter,
        "conversation_id": conversation_id,
        "conversation_type": conversation_type,
    }


async def _load_pending_task(
    session: AsyncSession,
    task_id: str,
    *,
    expected_task_type: str,
) -> Task:
    task = await session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError(f"Task not found: {task_id}")
    if task.task_type != expected_task_type:
        raise InvalidCommandTaskError(
            f"Expected {expected_task_type} task, got {task.task_type}"
        )
    if task.status != TaskStatus.PENDING.value:
        raise InvalidTaskStatusTransitionError(
            f"Task is not pending: {task.id} ({task.status})"
        )
    return task


async def _mark_running(session: AsyncSession, task: Task) -> None:
    task.status = TaskStatus.RUNNING.value
    task.result_text = None
    task.error_message = None
    await session.flush()


async def _succeed_task(session: AsyncSession, task: Task, result_text: str) -> Task:
    task.status = TaskStatus.SUCCESS.value
    task.result_text = result_text
    task.error_message = None
    await session.commit()
    await session.refresh(task)
    return task


async def _fail_task(session: AsyncSession, task: Task, error_message: str) -> Task:
    task.status = TaskStatus.FAILED.value
    task.result_text = None
    task.error_message = error_message
    await session.commit()
    await session.refresh(task)
    return task


def _command_rest(input_text: str, command: str) -> str:
    text = input_text.strip()
    if not text.startswith(command):
        raise InvalidCommandTaskError(f"Invalid command: {command}")
    return text.removeprefix(command).strip()


def _phase_label(status: str) -> str:
    return {
        TaskStatus.PENDING.value: "等待执行",
        TaskStatus.RUNNING.value: "执行中",
        TaskStatus.SUCCESS.value: "已完成",
        TaskStatus.FAILED.value: "执行失败",
        TaskStatus.CANCELLED.value: "已取消",
        TaskStatus.WAITING_APPROVAL.value: "等待审批",
    }.get(status, "未知")


def _safe_summary(
    value: object,
    *,
    extra_sensitive_values: Iterable[str | None] = (),
    limit: int = 1000,
) -> str:
    text = sanitize_text(value, extra_sensitive_values=extra_sensitive_values).strip()
    if "traceback" in text.lower():
        text = "内部错误已脱敏"
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _safe_json(
    payload: dict[str, Any],
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> str:
    return _safe_summary(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ),
        extra_sensitive_values=extra_sensitive_values,
    )
