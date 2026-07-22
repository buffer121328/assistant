from __future__ import annotations

from collections.abc import Iterable
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from common.redaction import sanitize_text
from application.task_lifecycle import (
    InvalidCommandTaskError,
    InvalidTaskStatusTransitionError,
    TaskNotFoundError,
)
from domain.models import Task, TaskStatus


async def _load_pending_task(
    session: AsyncSession,
    task_id: str,
    *,
    expected_task_type: str,
) -> Task:
    """执行 加载 pending task 的内部辅助逻辑。

    Args:
        session: session 参数。
        task_id: task_id 参数。
        expected_task_type: expected_task_type 参数。
    """
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
    """执行 标记 running 的内部辅助逻辑。

    Args:
        session: session 参数。
        task: task 参数。
    """
    task.status = TaskStatus.RUNNING.value
    task.result_text = None
    task.error_message = None
    await session.flush()


async def _succeed_task(session: AsyncSession, task: Task, result_text: str) -> Task:
    """执行 处理 succeed task 的内部辅助逻辑。

    Args:
        session: session 参数。
        task: task 参数。
        result_text: result_text 参数。
    """
    task.status = TaskStatus.SUCCESS.value
    task.result_text = result_text
    task.error_message = None
    await session.commit()
    await session.refresh(task)
    return task


async def _fail_task(session: AsyncSession, task: Task, error_message: str) -> Task:
    """执行 处理 fail task 的内部辅助逻辑。

    Args:
        session: session 参数。
        task: task 参数。
        error_message: error_message 参数。
    """
    task.status = TaskStatus.FAILED.value
    task.result_text = None
    task.error_message = error_message
    await session.commit()
    await session.refresh(task)
    return task


def _command_rest(input_text: str, command: str) -> str:
    """执行 处理 command rest 的内部辅助逻辑。

    Args:
        input_text: input_text 参数。
        command: command 参数。
    """
    text = input_text.strip()
    if not text.startswith(command):
        raise InvalidCommandTaskError(f"Invalid command: {command}")
    return text.removeprefix(command).strip()


def _phase_label(status: str) -> str:
    """执行 处理 phase label 的内部辅助逻辑。

    Args:
        status: status 参数。
    """
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
    """执行 处理 safe summary 的内部辅助逻辑。

    Args:
        value: value 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
        limit: limit 参数。
    """
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
    """执行 处理 safe json 的内部辅助逻辑。

    Args:
        payload: payload 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
    """
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
