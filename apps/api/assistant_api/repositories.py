from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    Memory,
    MemoryFeedback,
    MemoryIndexOutbox,
    MemoryLink,
    ModelLog,
    PlatformAccount,
    ProcessedMessage,
    ScheduledTaskRun,
    SkillAuditLog,
    Task,
    TaskStatus,
    ToolLog,
    User,
    utc_now,
)


@dataclass(frozen=True)
class TaskCreate:
    user_id: str
    platform: str
    task_type: str
    input_text: str
    workflow_key: str | None = None
    model_class: str | None = None
    conversation_id: str | None = None


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
            conversation_id=data.conversation_id,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def get_task(self, task_id: str) -> Task | None:
        return await self.session.get(Task, task_id)

    async def get_task_by_user(self, *, task_id: str, user_id: str) -> Task | None:
        return await self.session.scalar(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )

    async def get_latest_non_status_task(
        self,
        *,
        user_id: str,
        exclude_task_id: str,
    ) -> Task | None:
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
        result = await self.session.scalars(
            select(Task)
            .where(Task.user_id == user_id)
            .order_by(Task.created_at.desc(), Task.id.desc())
        )
        return list(result)


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_pending(self, *, task_id: str, tool_name: str) -> Approval:
        return await self.create_pending_request(
            task_id=task_id,
            approval_type=ApprovalType.TOOL.value,
            subject=tool_name,
            tool_name=tool_name,
            request_summary=f"工具调用：{tool_name}",
        )

    async def create_pending_request(
        self,
        *,
        task_id: str,
        approval_type: str,
        subject: str,
        tool_name: str,
        request_summary: str | None,
    ) -> Approval:
        approval = Approval(
            task_id=task_id,
            tool_name=tool_name,
            approval_type=approval_type,
            subject=subject,
            request_summary=request_summary,
            status=ApprovalStatus.PENDING.value,
        )
        self.session.add(approval)
        await self.session.flush()
        return approval

    async def get_active_for_tool(
        self,
        *,
        task_id: str,
        tool_name: str,
    ) -> Approval | None:
        return await self.session.scalar(
            select(Approval).where(
                Approval.task_id == task_id,
                Approval.tool_name == tool_name,
                Approval.approval_type == ApprovalType.TOOL.value,
                Approval.subject == tool_name,
                Approval.status.in_(
                    (
                        ApprovalStatus.PENDING.value,
                        ApprovalStatus.APPROVED.value,
                    )
                ),
            )
        )

    async def get_active_for_request(
        self,
        *,
        task_id: str,
        approval_type: str,
        subject: str,
    ) -> Approval | None:
        return await self.session.scalar(
            select(Approval).where(
                Approval.task_id == task_id,
                Approval.approval_type == approval_type,
                Approval.subject == subject,
                Approval.status.in_(
                    (
                        ApprovalStatus.PENDING.value,
                        ApprovalStatus.APPROVED.value,
                    )
                ),
            )
        )

    async def get_by_task(
        self,
        *,
        approval_id: str,
        task_id: str,
    ) -> Approval | None:
        return await self.session.scalar(
            select(Approval).where(
                Approval.id == approval_id,
                Approval.task_id == task_id,
            )
        )

    async def list_by_task(self, task_id: str) -> list[Approval]:
        result = await self.session.scalars(
            select(Approval)
            .where(Approval.task_id == task_id)
            .order_by(Approval.created_at.asc(), Approval.id.asc())
        )
        return list(result)


@dataclass(frozen=True)
class ProcessedMessageCreate:
    platform: str
    message_id: str
    reason: str
    chat_id: str | None = None
    response_target: str | None = None
    task_id: str | None = None


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user_id_by_platform_account(
        self,
        *,
        platform: str,
        platform_user_id: str,
    ) -> str | None:
        return await self.session.scalar(
            select(PlatformAccount.user_id).where(
                PlatformAccount.platform == platform,
                PlatformAccount.platform_user_id == platform_user_id,
            )
        )

    async def get_processed_message(
        self,
        *,
        platform: str,
        message_id: str,
    ) -> ProcessedMessage | None:
        return await self.session.scalar(
            select(ProcessedMessage).where(
                ProcessedMessage.platform == platform,
                ProcessedMessage.message_id == message_id,
            )
        )

    async def create_processed_message(
        self,
        data: ProcessedMessageCreate,
    ) -> ProcessedMessage:
        processed_message = ProcessedMessage(
            platform=data.platform,
            message_id=data.message_id,
            chat_id=data.chat_id,
            response_target=data.response_target,
            reason=data.reason,
            task_id=data.task_id,
        )
        self.session.add(processed_message)
        await self.session.flush()
        return processed_message

    async def get_task_dispatch_record(self, task_id: str) -> ProcessedMessage | None:
        return await self.session.scalar(
            select(ProcessedMessage)
            .where(
                ProcessedMessage.reason == "task_created",
                ProcessedMessage.task_id == task_id,
            )
            .order_by(ProcessedMessage.created_at.asc(), ProcessedMessage.id.asc())
            .limit(1)
        )


@dataclass(frozen=True)
class ModelLogCreate:
    task_id: str | None
    model_class: str | None
    request_text: str | None
    response_text: str | None
    error_message: str | None


class ModelLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_model_log(self, data: ModelLogCreate) -> ModelLog:
        model_log = ModelLog(
            task_id=data.task_id,
            model_class=data.model_class,
            request_text=data.request_text,
            response_text=data.response_text,
            error_message=data.error_message,
        )
        self.session.add(model_log)
        await self.session.flush()
        return model_log


@dataclass(frozen=True)
class MemoryCreate:
    user_id: str
    content: str
    normalized_content: str
    content_hash: str
    memory_type: str = "preference"
    status: str = "active"
    scope_kind: str = "user/global"
    scope_id: str | None = None
    sensitivity: str = "public"
    confirmed_by_user: bool = False
    confirmed_at: datetime | None = None
    source_kind: str = "explicit_service"
    source_trust: str = "trusted_user"
    source_spans_json: str = "[]"
    candidate_links_json: str = "[]"
    reason_code: str = "explicit_user_request"
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    source_task_id: str | None = None
    supersedes_id: str | None = None
    importance_score: int = 5
    expires_at: datetime | None = None


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_memory(self, data: MemoryCreate) -> Memory:
        memory = Memory(
            user_id=data.user_id,
            content=data.content,
            normalized_content=data.normalized_content,
            content_hash=data.content_hash,
            memory_type=data.memory_type,
            status=data.status,
            scope_kind=data.scope_kind,
            scope_id=data.scope_id,
            sensitivity=data.sensitivity,
            confirmed_by_user=data.confirmed_by_user,
            confirmed_at=data.confirmed_at,
            source_kind=data.source_kind,
            source_trust=data.source_trust,
            source_spans_json=data.source_spans_json,
            candidate_links_json=data.candidate_links_json,
            reason_code=data.reason_code,
            source_conversation_id=data.source_conversation_id,
            source_message_id=data.source_message_id,
            source_task_id=data.source_task_id,
            supersedes_id=data.supersedes_id,
            importance_score=data.importance_score,
            expires_at=data.expires_at,
            is_active=True,
        )
        self.session.add(memory)
        await self.session.flush()
        return memory

    async def get_by_source_message(
        self, *, user_id: str, source_kind: str, source_message_id: str
    ) -> Memory | None:
        return await self.session.scalar(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.source_kind == source_kind,
                Memory.source_message_id == source_message_id,
            )
        )

    async def queue_index_operation(
        self, *, memory: Memory, operation: str, error_code: str
    ) -> MemoryIndexOutbox:
        existing = await self.session.scalar(
            select(MemoryIndexOutbox).where(
                MemoryIndexOutbox.memory_id == memory.id,
                MemoryIndexOutbox.operation == operation,
                MemoryIndexOutbox.status == "pending",
            )
        )
        if existing is not None:
            return existing
        item = MemoryIndexOutbox(
            memory_id=memory.id,
            user_id=memory.user_id,
            operation=operation,
            status="pending",
            last_error_code=error_code,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_memories(
        self,
        *,
        user_id: str,
        status: str | None = None,
        scope_kind: str | None = None,
    ) -> list[Memory]:
        statement = select(Memory).where(Memory.user_id == user_id)
        if status is not None:
            statement = statement.where(Memory.status == status)
        if scope_kind is not None:
            statement = statement.where(Memory.scope_kind == scope_kind)
        result = await self.session.scalars(
            statement.order_by(Memory.created_at.asc(), Memory.id.asc())
        )
        return list(result)

    async def list_links_for_memory(self, *, memory_id: str) -> list[MemoryLink]:
        result = await self.session.scalars(
            select(MemoryLink)
            .where(
                or_(
                    MemoryLink.source_memory_id == memory_id,
                    MemoryLink.target_memory_id == memory_id,
                )
            )
            .order_by(MemoryLink.created_at.asc(), MemoryLink.id.asc())
        )
        return list(result)

    async def list_feedback_for_memory(
        self, *, memory_id: str, user_id: str
    ) -> list[MemoryFeedback]:
        result = await self.session.scalars(
            select(MemoryFeedback)
            .where(
                MemoryFeedback.memory_id == memory_id,
                MemoryFeedback.user_id == user_id,
            )
            .order_by(MemoryFeedback.created_at.asc(), MemoryFeedback.id.asc())
        )
        return list(result)

    async def list_index_outbox(
        self, *, user_id: str, status: str | None = None
    ) -> list[MemoryIndexOutbox]:
        statement = select(MemoryIndexOutbox).where(
            MemoryIndexOutbox.user_id == user_id
        )
        if status is not None:
            statement = statement.where(MemoryIndexOutbox.status == status)
        result = await self.session.scalars(
            statement.order_by(
                MemoryIndexOutbox.created_at.asc(), MemoryIndexOutbox.id.asc()
            )
        )
        return list(result)

    async def list_active_memories(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> list[Memory]:
        now = now or utc_now()
        result = await self.session.scalars(
            select(Memory)
            .where(Memory.user_id == user_id, *eligible_memory_conditions(now))
            .order_by(Memory.created_at.asc(), Memory.id.asc())
        )
        return list(result)

    async def get_memory_by_user(
        self, *, memory_id: str, user_id: str
    ) -> Memory | None:
        return await self.session.scalar(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        )

    async def create_link(
        self,
        *,
        source_memory_id: str,
        target_memory_id: str,
        link_type: str,
        created_by: str,
        confidence: float = 1.0,
        source_evidence_id: str | None = None,
    ) -> MemoryLink:
        existing = await self.session.scalar(
            select(MemoryLink).where(
                MemoryLink.source_memory_id == source_memory_id,
                MemoryLink.target_memory_id == target_memory_id,
                MemoryLink.link_type == link_type,
            )
        )
        if existing is not None:
            return existing
        link = MemoryLink(
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            link_type=link_type,
            created_by=created_by,
            confidence=confidence,
            source_evidence_id=source_evidence_id,
        )
        self.session.add(link)
        await self.session.flush()
        return link

    async def create_feedback(
        self,
        *,
        memory_id: str,
        user_id: str,
        feedback_type: str,
        task_id: str | None = None,
        conversation_id: str | None = None,
        retrieval_trace_id: str | None = None,
    ) -> MemoryFeedback:
        feedback = MemoryFeedback(
            memory_id=memory_id,
            user_id=user_id,
            feedback_type=feedback_type,
            task_id=task_id,
            conversation_id=conversation_id,
            retrieval_trace_id=retrieval_trace_id,
        )
        self.session.add(feedback)
        await self.session.flush()
        return feedback

    async def get_active_memory_by_user(
        self,
        *,
        memory_id: str,
        user_id: str,
    ) -> Memory | None:
        now = utc_now()
        return await self.session.scalar(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id == user_id,
                *eligible_memory_conditions(now),
            )
        )


def eligible_memory_conditions(now: datetime) -> tuple[ColumnElement[bool], ...]:
    return (
        Memory.is_active.is_(True),
        Memory.status == "active",
        Memory.deleted_at.is_(None),
        Memory.archived_at.is_(None),
        or_(Memory.expires_at.is_(None), Memory.expires_at > now),
    )


class ScheduledTaskRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_slot(
        self,
        *,
        schedule_key: str,
        scheduled_for: datetime,
    ) -> ScheduledTaskRun | None:
        return await self.session.scalar(
            select(ScheduledTaskRun).where(
                ScheduledTaskRun.schedule_key == schedule_key,
                ScheduledTaskRun.scheduled_for == scheduled_for,
            )
        )

    async def create(
        self,
        *,
        schedule_key: str,
        scheduled_for: datetime,
        task_id: str,
    ) -> ScheduledTaskRun:
        run = ScheduledTaskRun(
            schedule_key=schedule_key,
            scheduled_for=scheduled_for,
            task_id=task_id,
        )
        self.session.add(run)
        await self.session.flush()
        return run


@dataclass(frozen=True)
class ToolLogCreate:
    task_id: str | None
    tool_name: str
    status: str
    input_text: str | None = None
    output_text: str | None = None
    error_message: str | None = None


class ToolLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_tool_log(self, data: ToolLogCreate) -> ToolLog:
        tool_log = ToolLog(
            task_id=data.task_id,
            tool_name=data.tool_name,
            status=data.status,
            input_text=data.input_text,
            output_text=data.output_text,
            error_message=data.error_message,
        )
        self.session.add(tool_log)
        await self.session.flush()
        return tool_log

    async def has_successful_tool_log(self, *, task_id: str, tool_name: str) -> bool:
        existing = await self.session.scalar(
            select(ToolLog.id)
            .where(
                ToolLog.task_id == task_id,
                ToolLog.tool_name == tool_name,
                ToolLog.status == "succeeded",
            )
            .limit(1)
        )
        return existing is not None


class SkillAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def user_exists(self, user_id: str) -> bool:
        return await self.session.get(User, user_id) is not None

    async def create_started(
        self,
        *,
        actor_user_id: str,
        skill_name: str | None,
        action: str,
    ) -> SkillAuditLog:
        audit = SkillAuditLog(
            actor_user_id=actor_user_id,
            skill_name=skill_name,
            action=action,
            status="started",
        )
        self.session.add(audit)
        await self.session.flush()
        return audit

    async def finish(
        self,
        audit_id: str,
        *,
        status: str,
        skill_name: str | None,
        version: str | None,
        error_code: str | None,
    ) -> SkillAuditLog:
        audit = await self.session.get(SkillAuditLog, audit_id)
        if audit is None:
            raise RuntimeError("Skill audit record is unavailable")
        audit.status = status
        audit.skill_name = skill_name
        audit.version = version
        audit.error_code = error_code
        await self.session.flush()
        return audit
