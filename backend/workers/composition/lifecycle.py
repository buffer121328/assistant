from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.policies.redaction import sanitize_text
from domain.models import AgentRun, Task, TaskStatus, utc_now
from infrastructure.settings.config import Settings


async def start_agent_run(session: AsyncSession, task: Task) -> AgentRun:
    """Create a new AgentRun attempt for a task."""
    last_error: IntegrityError | None = None
    for _ in range(3):
        attempt_no = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(AgentRun.attempt_no), 0)).where(
                        AgentRun.task_id == task.id
                    )
                )
                or 0
            )
            + 1
        )
        agent_run = AgentRun(
            task_id=task.id,
            user_id=task.user_id,
            attempt_no=attempt_no,
            status="running",
            agent_profile=None,
            graph_version="langgraph-v2",
            checkpoint_id=None,
            tool_snapshot_revision=None,
            model_class=task.model_class,
        )
        session.add(agent_run)
        try:
            await session.commit()
        except IntegrityError as exc:
            last_error = exc
            await session.rollback()
            continue
        await session.refresh(agent_run)
        return agent_run
    assert last_error is not None
    raise last_error


async def finish_agent_run(
    session: AsyncSession,
    *,
    agent_run: AgentRun,
    task: Task,
    sensitive_values: tuple[str | None, ...],
) -> AgentRun:
    """Mark an AgentRun with the final task state."""
    agent_run.status = task.status
    agent_run.ended_at = utc_now()
    agent_run.model_class = task.model_class
    agent_run.error_message = (
        safe_worker_summary(task.error_message, sensitive_values=sensitive_values)
        if task.error_message
        else None
    )
    await session.commit()
    await session.refresh(agent_run)
    return agent_run


async def record_worker_failure(
    session: AsyncSession,
    *,
    task_id: str,
    error: Exception,
    sensitive_values: tuple[str | None, ...],
) -> Task:
    """Persist a sanitized worker failure on a task when possible."""
    await session.rollback()
    task = await session.get(Task, task_id)
    if task is None:
        raise error

    if task.status not in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}:
        return task

    if task.status == TaskStatus.PENDING.value:
        task.status = TaskStatus.RUNNING.value
        task.error_message = None
        task.result_text = None
        await session.flush()

    task.status = TaskStatus.FAILED.value
    task.result_text = None
    task.error_message = safe_worker_summary(error, sensitive_values=sensitive_values)
    await session.commit()
    await session.refresh(task)
    return task


async def sanitize_failed_task(
    session: AsyncSession,
    *,
    task: Task,
    sensitive_values: tuple[str | None, ...],
) -> Task:
    """Ensure failed task errors are redacted before later observers read them."""
    if task.status != TaskStatus.FAILED.value or task.error_message is None:
        return task

    safe_error = safe_worker_summary(
        task.error_message,
        sensitive_values=sensitive_values,
    )
    if safe_error == task.error_message:
        return task

    task.error_message = safe_error
    await session.commit()
    await session.refresh(task)
    return task


def safe_worker_summary(
    value: object,
    *,
    sensitive_values: tuple[str | None, ...],
    limit: int = 1000,
) -> str:
    """Return a bounded, redacted summary safe for task and run records."""
    text = sanitize_text(value, extra_sensitive_values=sensitive_values).strip()
    if "traceback" in text.lower():
        text = "内部错误已脱敏"
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def sensitive_values(settings: Settings) -> tuple[str | None, ...]:
    """Collect configured sensitive values used for runtime redaction."""
    return (
        settings.langbot_webhook_secret,
        settings.langbot_api_base_url,
        settings.langbot_api_key,
        settings.tavily_base_url,
        settings.tavily_api_key,
        settings.brave_search_api_key,
        settings.brave_search_base_url,
        settings.duckduckgo_search_base_url,
        settings.deepseek_api_key,
        settings.credential_master_key.get_secret_value(),
        settings.local_api_token.get_secret_value(),
    )
