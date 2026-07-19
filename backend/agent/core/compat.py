from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent.memory import SemanticMemory

from agent.ports import ConversationContextPack


async def record_execution_trace(
    session: AsyncSession,
    *,
    task_id: str,
    tool_name: str,
    status: str,
    input_text: str,
    output_text: str | None,
    error_message: str | None,
) -> None:
    from domain.models import ToolLog

    session.add(
        ToolLog(
            task_id=task_id,
            tool_name=tool_name,
            status=status,
            input_text=input_text,
            output_text=output_text,
            error_message=error_message,
        )
    )
    await session.flush()


async def execute_memory_task(
    session: AsyncSession,
    *,
    task_id: str,
    semantic_memory: SemanticMemory | None,
) -> Any:
    from domain.services import MemoryService

    return await MemoryService(
        session,
        semantic_memory=semantic_memory,
    ).execute_task(task_id)


async def execute_status_task(session: AsyncSession, *, task_id: str) -> Any:
    from domain.services import StatusService

    return await StatusService(session).execute_task(task_id)


def task_lifecycle(
    session: AsyncSession,
    *,
    success_hook: Callable[[Any], Awaitable[None]] | None,
) -> Any:
    from domain.services import TaskService

    return TaskService(session, success_hook=success_hook)


async def load_pending_task(
    session: AsyncSession,
    *,
    task_id: str,
    pending_status: str,
    not_pending_error: type[Exception],
    not_found_error: type[Exception],
) -> Any:
    from domain.models import Task

    task = await session.get(Task, task_id)
    if task is None:
        raise not_found_error(f"Task not found: {task_id}")
    if task.status != pending_status:
        raise not_pending_error(f"Task is not pending: {task.id} ({task.status})")
    return task


async def load_user(
    session: AsyncSession,
    *,
    user_id: str,
    not_found_error: type[Exception],
) -> Any:
    from domain.models import User

    user = await session.get(User, user_id)
    if user is None:
        raise not_found_error(f"User not found: {user_id}")
    return user


async def load_conversation_context(
    session: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    task_id: str,
    current_input: str,
    long_term_memory: str,
) -> ConversationContextPack:
    from domain.conversation_memory import ConversationMemoryService
    from domain.conversations import ConversationService
    from agent.memory.working_set import ConversationMessageRef, build_context_pack

    messages = await ConversationService(session).list_messages(
        conversation_id=conversation_id,
        user_id=user_id,
        limit=200,
        exclude_task_id=task_id,
    )
    conversation_memory = ConversationMemoryService(session)
    summary = await conversation_memory.ensure_summary_current(
        conversation_id=conversation_id,
        user_id=user_id,
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
