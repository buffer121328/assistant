from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from tasks.commands import _safe_json, _safe_summary
from tasks.lifecycle import TaskNotFoundError
from domain.policies.task_status import DISPATCHABLE_TASK_STATUSES
from domain.models import ProcessedMessage, Task, TaskStatus
from infrastructure.repositories import (
    MessageRepository,
    TaskRepository,
    ToolLogCreate,
    ToolLogRepository,
)


class LangBotMessageClientProtocol(Protocol):
    """表示 处理 lang bot message client protocol 的后端数据结构或服务对象。"""

    async def send_message(
        self,
        *,
        adapter: str,
        conversation_id: str,
        conversation_type: str,
        text: str,
        idempotency_key: str | None = None,
    ) -> Any:
        """处理 send message。

        Args:
            adapter: adapter 参数。
            conversation_id: conversation_id 参数。
            conversation_type: conversation_type 参数。
            text: text 参数。
            idempotency_key: idempotency_key 参数。
        """
        pass


@dataclass(frozen=True)
class DispatchResult:
    """表示 分发 result 的后端数据结构或服务对象。"""

    status: str
    message: str


class ResultDispatcher:
    """表示 处理 result dispatcher 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        langbot_client: LangBotMessageClientProtocol | None = None,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            langbot_client: langbot_client 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.session = session
        self.langbot_client = langbot_client
        self.sensitive_values = tuple(sensitive_values)
        self.task_repository = TaskRepository(session)
        self.webhook_repository = MessageRepository(session)
        self.tool_log_repository = ToolLogRepository(session)

    async def dispatch_task(self, task_id: str) -> DispatchResult:
        """分发 task。

        Args:
            task_id: task_id 参数。
        """
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

        dispatch_record = await self.webhook_repository.get_task_dispatch_record(
            task.id
        )
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
            safe_error = _safe_summary(
                exc, extra_sensitive_values=self.sensitive_values
            )
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
        """执行 构建 message 的内部辅助逻辑。

        Args:
            task: task 参数。
        """
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
        """执行 处理 send message 的内部辅助逻辑。

        Args:
            task: task 参数。
            target: target 参数。
            outbound_text: outbound_text 参数。
        """
        if task.platform == "langbot":
            if self.langbot_client is None:
                raise RuntimeError("LangBot client is not configured")
            return await self.langbot_client.send_message(
                adapter=target["adapter"],
                conversation_id=target["conversation_id"],
                conversation_type=target["conversation_type"],
                text=outbound_text,
                idempotency_key=task.id,
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
        """执行 记录 dispatch 的内部辅助逻辑。

        Args:
            task: task 参数。
            tool_name: tool_name 参数。
            target_payload: target_payload 参数。
            status: status 参数。
            output_text: output_text 参数。
            error_message: error_message 参数。
        """
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
        await self.webhook_repository.record_delivery_attempt(
            task_id=task.id,
            status=status,
            delivery_status=(
                "succeeded"
                if status == "succeeded"
                else ("failed" if target_payload is None else "retry")
            ),
            error_summary=error_message,
            result_json=output_text,
        )


def _dispatch_tool_name(task: Task) -> str:
    """执行 分发 tool name 的内部辅助逻辑。

    Args:
        task: task 参数。
    """
    if task.platform == "langbot" and task.status == TaskStatus.WAITING_APPROVAL.value:
        return "langbot.approval_dispatch"
    if task.platform == "langbot":
        return "langbot.result_dispatch"
    raise ValueError(f"Unsupported dispatch platform: {task.platform}")


def _missing_target_message(platform: str) -> str:
    """执行 处理 missing target message 的内部辅助逻辑。

    Args:
        platform: platform 参数。
    """
    return {
        "langbot": "缺少 LangBot 推送目标",
    }.get(platform, "缺少推送目标")


def _resolve_dispatch_target(
    *,
    task: Task,
    dispatch_record: ProcessedMessage | None,
) -> dict[str, str] | None:
    """执行 解析 dispatch target 的内部辅助逻辑。

    Args:
        task: task 参数。
        dispatch_record: dispatch_record 参数。
    """
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
