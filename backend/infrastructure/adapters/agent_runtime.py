from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from memory import SemanticMemory

from agent.ports import (
    ConversationContextPack,
    ConversationContextPort,
    ExecutionTracePort,
    LocalTaskServicePort,
    TaskLifecyclePort,
    UserLookupPort,
)

from domain.models import Task, TaskStatus, ToolLog, User
from memory.user_memory import MemoryService
from tasks.status import StatusService
from tasks.lifecycle import (
    InvalidTaskStatusTransitionError,
    TaskNotFoundError,
    TaskService,
    UserNotFoundError,
)

if TYPE_CHECKING:
    from memory.working_set import ConversationCompactionPolicy
    from session.memory_service import ConversationSummarizer


class SqlAlchemyUserLookupPort(UserLookupPort[User]):
    """表示 处理 sql alchemy user lookup port 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def load_user(self, user_id: str) -> User:
        """加载 user。

        Args:
            user_id: user_id 参数。
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise UserNotFoundError(f"User not found: {user_id}")
        return user


class SqlAlchemyExecutionTracePort(ExecutionTracePort):
    """表示 处理 sql alchemy execution trace port 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
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
        """记录 trace。

        Args:
            task_id: task_id 参数。
            tool_name: tool_name 参数。
            status: status 参数。
            input_text: input_text 参数。
            output_text: output_text 参数。
            error_message: error_message 参数。
        """
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
    """表示 处理 sql alchemy task lifecycle port 的后端数据结构或服务对象。"""

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
        self.success_hook = success_hook

    async def load_pending(self, task_id: str) -> Task:
        """加载 pending。

        Args:
            task_id: task_id 参数。
        """
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
        """标记 running。

        Args:
            task_id: task_id 参数。
            workflow_key: workflow_key 参数。
        """
        task = await self.load_pending(task_id)
        task.status = TaskStatus.RUNNING.value
        task.workflow_key = workflow_key
        task.error_message = None
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def save_success(self, task_id: str, result_text: str) -> Task:
        """保存 success。

        Args:
            task_id: task_id 参数。
            result_text: result_text 参数。
        """
        return await TaskService(
            self.session,
            success_hook=self.success_hook,
        ).save_success(task_id, result_text)

    async def save_failure(self, task_id: str, error_message: str) -> Task:
        """保存 failure。

        Args:
            task_id: task_id 参数。
            error_message: error_message 参数。
        """
        return await TaskService(self.session).save_failure(task_id, error_message)

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
        return await TaskService(self.session).save_waiting_approval(
            task_id,
            message,
            requested_tools=requested_tools,
            approval_requests=approval_requests,
        )


class SqlAlchemyLocalTaskServicePort(LocalTaskServicePort[Task]):
    """表示 处理 sql alchemy local task service port 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        semantic_memory: SemanticMemory | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            semantic_memory: semantic_memory 参数。
        """
        self.session = session
        self.semantic_memory = semantic_memory

    async def execute_memory_task(self, task_id: str) -> Task:
        """执行 memory task。

        Args:
            task_id: task_id 参数。
        """
        return await MemoryService(
            self.session,
            semantic_memory=self.semantic_memory,
        ).execute_task(task_id)

    async def execute_status_task(self, task_id: str) -> Task:
        """执行 status task。

        Args:
            task_id: task_id 参数。
        """
        return await StatusService(self.session).execute_task(task_id)


class SqlAlchemyConversationContextPort(ConversationContextPort):
    """表示 处理 sql alchemy conversation context port 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        compaction_policy: ConversationCompactionPolicy | None = None,
        summarizer: ConversationSummarizer | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            compaction_policy: compaction_policy 参数。
            summarizer: summarizer 参数。
        """
        self.session = session
        self.compaction_policy = compaction_policy
        self.summarizer = summarizer

    async def load_context(
        self,
        *,
        conversation_id: str,
        user_id: str,
        task_id: str,
        current_input: str,
        long_term_memory: str,
    ) -> ConversationContextPack:
        """加载 context。

        Args:
            conversation_id: conversation_id 参数。
            user_id: user_id 参数。
            task_id: task_id 参数。
            current_input: current_input 参数。
            long_term_memory: long_term_memory 参数。
        """
        from memory.working_set import (
            ConversationMessageRef,
            build_context_pack,
        )

        from session.memory_service import ConversationMemoryService
        from session.conversations import ConversationService

        messages = await ConversationService(self.session).list_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=200,
            exclude_task_id=task_id,
        )
        conversation_memory = ConversationMemoryService(self.session)
        summary = await conversation_memory.ensure_summary_current(
            conversation_id=conversation_id,
            user_id=user_id,
            summarizer=self.summarizer,
            policy=self.compaction_policy,
            exclude_task_id=task_id,
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
            history=tuple(
                (message.role, message.content) for message in pack.recent_turns
            ),
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
