from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from domain.models import (
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
    SkillAuditLog,
    Task,
    TaskStatus,
    ToolLog,
    User,
    utc_now,
)


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


class ApprovalRepository:
    """表示 处理 approval repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def create_pending(self, *, task_id: str, tool_name: str) -> Approval:
        """创建 pending。

        Args:
            task_id: task_id 参数。
            tool_name: tool_name 参数。
        """
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
        """创建 pending request。

        Args:
            task_id: task_id 参数。
            approval_type: approval_type 参数。
            subject: subject 参数。
            tool_name: tool_name 参数。
            request_summary: request_summary 参数。
        """
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
        """获取 active for tool。

        Args:
            task_id: task_id 参数。
            tool_name: tool_name 参数。
        """
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
        """获取 active for request。

        Args:
            task_id: task_id 参数。
            approval_type: approval_type 参数。
            subject: subject 参数。
        """
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
        """获取 by task。

        Args:
            approval_id: approval_id 参数。
            task_id: task_id 参数。
        """
        return await self.session.scalar(
            select(Approval).where(
                Approval.id == approval_id,
                Approval.task_id == task_id,
            )
        )

    async def list_by_task(self, task_id: str) -> list[Approval]:
        """列出 by task。

        Args:
            task_id: task_id 参数。
        """
        result = await self.session.scalars(
            select(Approval)
            .where(Approval.task_id == task_id)
            .order_by(Approval.created_at.asc(), Approval.id.asc())
        )
        return list(result)


@dataclass(frozen=True)
class ProcessedMessageCreate:
    """表示 处理 processed message create 的后端数据结构或服务对象。"""

    platform: str
    message_id: str
    reason: str
    adapter: str | None = None
    sender_id: str | None = None
    conversation_type: str | None = None
    message_text: str | None = None
    intent_outcome: str | None = None
    chat_id: str | None = None
    response_target: str | None = None
    task_id: str | None = None
    delivery_status: str | None = None
    delivery_error_summary: str | None = None
    delivery_result_json: str | None = None


class MessageRepository:
    """表示 处理 message repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def get_user_id_by_platform_account(
        self,
        *,
        platform: str,
        platform_user_id: str,
    ) -> str | None:
        """获取 user id by platform account。

        Args:
            platform: platform 参数。
            platform_user_id: platform_user_id 参数。
        """
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
        adapter: str | None,
        message_id: str,
    ) -> ProcessedMessage | None:
        """获取 processed message。

        Args:
            platform: platform 参数。
            adapter: adapter 参数。
            message_id: message_id 参数。
        """
        return await self.session.scalar(
            select(ProcessedMessage).where(
                ProcessedMessage.platform == platform,
                ProcessedMessage.adapter == adapter,
                ProcessedMessage.message_id == message_id,
            )
        )

    async def create_processed_message(
        self,
        data: ProcessedMessageCreate,
    ) -> ProcessedMessage:
        """创建 processed message。

        Args:
            data: data 参数。
        """
        processed_message = ProcessedMessage(
            platform=data.platform,
            message_id=data.message_id,
            adapter=data.adapter,
            sender_id=data.sender_id,
            conversation_type=data.conversation_type,
            message_text=data.message_text,
            intent_outcome=data.intent_outcome,
            chat_id=data.chat_id,
            response_target=data.response_target,
            reason=data.reason,
            task_id=data.task_id,
            delivery_status=data.delivery_status,
            delivery_error_summary=data.delivery_error_summary,
            delivery_result_json=data.delivery_result_json,
        )
        self.session.add(processed_message)
        await self.session.flush()
        return processed_message

    async def get_task_dispatch_record(self, task_id: str) -> ProcessedMessage | None:
        """获取 task dispatch record。

        Args:
            task_id: task_id 参数。
        """
        return await self.session.scalar(
            select(ProcessedMessage)
            .where(
                ProcessedMessage.reason == "task_created",
                ProcessedMessage.task_id == task_id,
            )
            .order_by(ProcessedMessage.created_at.asc(), ProcessedMessage.id.asc())
            .limit(1)
        )

    async def list_recent_bridge_sessions(
        self,
        *,
        limit: int = 20,
    ) -> list[ProcessedMessage]:
        """列出 recent bridge sessions。

        Args:
            limit: limit 参数。
        """
        result = await self.session.scalars(
            select(ProcessedMessage)
            .where(ProcessedMessage.platform == "langbot")
            .order_by(ProcessedMessage.created_at.desc(), ProcessedMessage.id.desc())
            .limit(limit)
        )
        return list(result)

    async def get_bridge_session(self, message_id: str) -> ProcessedMessage | None:
        """获取 bridge session。

        Args:
            message_id: message_id 参数。
        """
        return await self.session.scalar(
            select(ProcessedMessage).where(
                ProcessedMessage.platform == "langbot",
                ProcessedMessage.message_id == message_id,
            )
        )

    async def record_delivery_attempt(
        self,
        *,
        task_id: str,
        status: str,
        error_summary: str | None = None,
        result_json: str | None = None,
        delivery_status: str | None = None,
    ) -> ProcessedMessage | None:
        """记录 delivery attempt。

        Args:
            task_id: task_id 参数。
            status: status 参数。
            error_summary: error_summary 参数。
            result_json: result_json 参数。
            delivery_status: delivery_status 参数。
        """
        record = await self.get_task_dispatch_record(task_id)
        if record is None:
            return None

        record.delivery_attempt_count += 1
        record.delivery_status = delivery_status or status
        record.delivery_error_summary = error_summary
        record.delivery_result_json = result_json
        record.delivery_last_attempt_at = utc_now()
        return record


@dataclass(frozen=True)
class ModelLogCreate:
    """表示 处理 model log create 的后端数据结构或服务对象。"""

    task_id: str | None
    model_class: str | None
    request_text: str | None
    response_text: str | None
    error_message: str | None
    agent_run_id: str | None = None


class ModelLogRepository:
    """表示 处理 model log repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def create_model_log(self, data: ModelLogCreate) -> ModelLog:
        """创建 model log。

        Args:
            data: data 参数。
        """
        model_log = ModelLog(
            task_id=data.task_id,
            agent_run_id=data.agent_run_id,
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
    """表示 处理 memory create 的后端数据结构或服务对象。"""

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
    """表示 处理 memory repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def create_memory(self, data: MemoryCreate) -> Memory:
        """创建 memory。

        Args:
            data: data 参数。
        """
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
        """获取 by source message。

        Args:
            user_id: user_id 参数。
            source_kind: source_kind 参数。
            source_message_id: source_message_id 参数。
        """
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
        """处理 queue index operation。

        Args:
            memory: memory 参数。
            operation: operation 参数。
            error_code: error_code 参数。
        """
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
        """列出 memories。

        Args:
            user_id: user_id 参数。
            status: status 参数。
            scope_kind: scope_kind 参数。
        """
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
        """列出 links for memory。

        Args:
            memory_id: memory_id 参数。
        """
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
        """列出 feedback for memory。

        Args:
            memory_id: memory_id 参数。
            user_id: user_id 参数。
        """
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
        """列出 index outbox。

        Args:
            user_id: user_id 参数。
            status: status 参数。
        """
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
        """列出 active memories。

        Args:
            user_id: user_id 参数。
            now: now 参数。
        """
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
        """获取 memory by user。

        Args:
            memory_id: memory_id 参数。
            user_id: user_id 参数。
        """
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
        """创建 link。

        Args:
            source_memory_id: source_memory_id 参数。
            target_memory_id: target_memory_id 参数。
            link_type: link_type 参数。
            created_by: created_by 参数。
            confidence: confidence 参数。
            source_evidence_id: source_evidence_id 参数。
        """
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
        """创建 feedback。

        Args:
            memory_id: memory_id 参数。
            user_id: user_id 参数。
            feedback_type: feedback_type 参数。
            task_id: task_id 参数。
            conversation_id: conversation_id 参数。
            retrieval_trace_id: retrieval_trace_id 参数。
        """
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
        """获取 active memory by user。

        Args:
            memory_id: memory_id 参数。
            user_id: user_id 参数。
        """
        now = utc_now()
        return await self.session.scalar(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id == user_id,
                *eligible_memory_conditions(now),
            )
        )


def eligible_memory_conditions(now: datetime) -> tuple[ColumnElement[bool], ...]:
    """处理 eligible memory conditions。

    Args:
        now: now 参数。
    """
    return (
        Memory.is_active.is_(True),
        Memory.status == "active",
        Memory.deleted_at.is_(None),
        Memory.archived_at.is_(None),
        or_(Memory.expires_at.is_(None), Memory.expires_at > now),
    )


@dataclass(frozen=True)
class ToolLogCreate:
    """表示 处理 tool log create 的后端数据结构或服务对象。"""

    task_id: str | None
    tool_name: str
    status: str
    input_text: str | None = None
    output_text: str | None = None
    error_message: str | None = None


class ToolLogRepository:
    """表示 处理 tool log repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def create_tool_log(self, data: ToolLogCreate) -> ToolLog:
        """创建 tool log。

        Args:
            data: data 参数。
        """
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
        """处理 has successful tool log。

        Args:
            task_id: task_id 参数。
            tool_name: tool_name 参数。
        """
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
    """表示 处理 skill audit repository 的后端数据结构或服务对象。"""

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

    async def create_started(
        self,
        *,
        actor_user_id: str,
        skill_name: str | None,
        action: str,
    ) -> SkillAuditLog:
        """创建 started。

        Args:
            actor_user_id: actor_user_id 参数。
            skill_name: skill_name 参数。
            action: action 参数。
        """
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
        """处理 finish。

        Args:
            audit_id: audit_id 参数。
            status: status 参数。
            skill_name: skill_name 参数。
            version: version 参数。
            error_code: error_code 参数。
        """
        audit = await self.session.get(SkillAuditLog, audit_id)
        if audit is None:
            raise RuntimeError("Skill audit record is unavailable")
        audit.status = status
        audit.skill_name = skill_name
        audit.version = version
        audit.error_code = error_code
        await self.session.flush()
        return audit
