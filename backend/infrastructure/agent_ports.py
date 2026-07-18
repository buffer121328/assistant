from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from agent.memory import SemanticMemory

from agent.ports import (
    ConversationContextPack,
    ConversationContextPort,
    ExecutionTracePort,
    LocalTaskServicePort,
    TaskLifecyclePort,
    UserLookupPort,
)

from domain.models import Task, TaskStatus, ToolLog, User
from domain.services import MemoryService, StatusService
from domain.task_lifecycle import (
    InvalidTaskStatusTransitionError,
    TaskNotFoundError,
    TaskService,
    UserNotFoundError,
)


class SqlAlchemyUserLookupPort(UserLookupPort[User]):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_user(self, user_id: str) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise UserNotFoundError(f"User not found: {user_id}")
        return user


class SqlAlchemyExecutionTracePort(ExecutionTracePort):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_trace(
        self,
        *,
        task_id: str,
        tool_name: str,
        status: str,
        input_text: str,
        output_text: str | None,
        error_message: str | None,
    ) -> None:
        self.session.add(
            ToolLog(
                task_id=task_id,
                tool_name=tool_name,
                status=status,
                input_text=input_text,
                output_text=output_text,
                error_message=error_message,
            )
        )
        await self.session.flush()


class SqlAlchemyTaskLifecyclePort(TaskLifecyclePort[Task]):
    def __init__(
        self,
        session: AsyncSession,
        *,
        success_hook: Callable[[Task], Awaitable[None]] | None = None,
    ) -> None:
        self.session = session
        self.success_hook = success_hook

    async def load_pending(self, task_id: str) -> Task:
        task = await self.session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        if task.status != TaskStatus.PENDING.value:
            raise InvalidTaskStatusTransitionError(
                f"Task is not pending: {task.id} ({task.status})"
            )
        return task

    async def mark_running(
        self, task_id: str, *, workflow_key: str | None = None
    ) -> Task:
        task = await self.load_pending(task_id)
        task.status = TaskStatus.RUNNING.value
        task.workflow_key = workflow_key
        task.error_message = None
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def save_success(self, task_id: str, result_text: str) -> Task:
        return await TaskService(
            self.session,
            success_hook=self.success_hook,
        ).save_success(task_id, result_text)

    async def save_failure(self, task_id: str, error_message: str) -> Task:
        return await TaskService(self.session).save_failure(task_id, error_message)

    async def save_waiting_approval(
        self,
        task_id: str,
        message: str,
        *,
        requested_tools: Iterable[str] = (),
        approval_requests: Iterable[object] = (),
    ) -> Task:
        return await TaskService(self.session).save_waiting_approval(
            task_id,
            message,
            requested_tools=requested_tools,
            approval_requests=approval_requests,
        )


class SqlAlchemyLocalTaskServicePort(LocalTaskServicePort[Task]):
    def __init__(
        self,
        session: AsyncSession,
        *,
        semantic_memory: SemanticMemory | None = None,
    ) -> None:
        self.session = session
        self.semantic_memory = semantic_memory

    async def execute_memory_task(self, task_id: str) -> Task:
        return await MemoryService(
            self.session,
            semantic_memory=self.semantic_memory,
        ).execute_task(task_id)

    async def execute_status_task(self, task_id: str) -> Task:
        return await StatusService(self.session).execute_task(task_id)


class SqlAlchemyConversationContextPort(ConversationContextPort):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_context(
        self,
        *,
        conversation_id: str,
        user_id: str,
        task_id: str,
        current_input: str,
        long_term_memory: str,
    ) -> ConversationContextPack:
        from agent.memory.working_set import (
            ConversationMessageRef,
            build_context_pack,
        )

        from domain.conversation_memory import ConversationMemoryService
        from domain.conversations import ConversationService

        messages = await ConversationService(self.session).list_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=200,
            exclude_task_id=task_id,
        )
        conversation_memory = ConversationMemoryService(self.session)
        summary = await conversation_memory.get_active_summary(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        blocks = await conversation_memory.list_blocks(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        pack = build_context_pack(
            memory_blocks=tuple((block.id, block.content) for block in blocks),
            conversation_summary=summary.summary_text if summary else "",
            summary_source_ids=(
                (summary.source_start_message_id, summary.source_end_message_id)
                if summary
                else ()
            ),
            summary_version=summary.summary_version if summary else None,
            long_term_memory=long_term_memory,
            messages=tuple(
                ConversationMessageRef(message.id, message.role, message.content)
                for message in messages
            ),
            current_input=current_input,
        )
        return ConversationContextPack(
            history=tuple((message.role, message.content) for message in pack.recent_turns),
            summary=pack.conversation_summary,
            memory_blocks=pack.memory_blocks,
            trace=tuple(
                {
                    "section": item.section,
                    "estimated_tokens": item.estimated_tokens,
                    "source_ids": item.source_ids,
                    "truncated_source_ids": item.truncated_source_ids,
                    "version": item.version,
                }
                for item in pack.trace
            ),
            compacted=pack.compacted,
        )
