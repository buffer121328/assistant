from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import Task, TaskEvent, TaskStatus, ToolLog
from domain.services import TaskNotFoundError, TaskService
from domain.task_events import TaskEventRepository, event_record
from model_gateway import sanitize_text

from .catalog import ToolDescriptor
from .registry import ToolInvocation, ToolRiskLevel, ToolSpec


TASK_TOOL_VERSION = "v10-task-tools-v1"
TERMINAL = {TaskStatus.SUCCESS.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
CANCELLABLE = {TaskStatus.PENDING.value, TaskStatus.RUNNING.value, TaskStatus.WAITING_APPROVAL.value}


@dataclass
class AgentTaskToolService:
    session: AsyncSession
    max_events: int = 20
    max_result_chars: int = 4000

    async def start_background(
        self,
        *,
        user_id: str,
        conversation_id: str,
        task_type: str,
        input_text: str,
        workflow_key: str | None = None,
        model_class: str | None = None,
    ) -> dict[str, object]:
        if not conversation_id:
            raise ValueError("conversation_id is required")
        task = await TaskService(self.session).create_task(
            user_id=user_id,
            platform="agent_background",
            task_type=task_type,
            input_text=input_text,
            workflow_key=workflow_key,
            model_class=model_class,
            conversation_id=conversation_id,
            commit=False,
        )
        await TaskEventRepository(self.session).append(
            task_id=task.id,
            user_id=user_id,
            event_type="queued",
            payload={"source": "task.start_background", "task_type": task_type},
        )
        await self._tool_log(
            task_id=task.id,
            name="task.start_background",
            status="succeeded",
            output={"task_id": task.id, "status": task.status},
        )
        await self.session.commit()
        await self.session.refresh(task)
        return {"task_id": task.id, "status": task.status, "queued": True}

    async def check_status(self, *, user_id: str, task_id: str) -> dict[str, object]:
        task = await self._owned_task(user_id=user_id, task_id=task_id)
        events = await self._recent_events(task_id=task.id)
        return {
            "task_id": task.id,
            "status": task.status,
            "task_type": task.task_type,
            "progress": self._progress(task.status),
            "events": [event_record(item) for item in events],
        }

    async def get_result(self, *, user_id: str, task_id: str) -> dict[str, object]:
        task = await self._owned_task(user_id=user_id, task_id=task_id)
        result = task.result_text if task.status in TERMINAL else None
        return {
            "task_id": task.id,
            "status": task.status,
            "result_text": _truncate(result or "", self.max_result_chars) if result else None,
            "error_message": _truncate(task.error_message or "", self.max_result_chars) if task.error_message else None,
            "terminal": task.status in TERMINAL,
        }

    async def cancel(
        self,
        *,
        user_id: str,
        task_id: str,
        reason: str = "cancelled by user",
    ) -> dict[str, object]:
        task = await self._owned_task(user_id=user_id, task_id=task_id)
        if task.status not in CANCELLABLE:
            return {"task_id": task.id, "status": task.status, "cancelled": False, "reason": "terminal"}
        task.status = TaskStatus.CANCELLED.value
        task.error_message = None
        task.result_text = sanitize_text(reason)[:1000]
        await TaskEventRepository(self.session).append(
            task_id=task.id,
            user_id=user_id,
            event_type="cancelled",
            payload={"reason": reason},
        )
        await self._tool_log(
            task_id=task.id,
            name="task.cancel",
            status="succeeded",
            output={"task_id": task.id, "status": task.status},
        )
        await self.session.commit()
        await self.session.refresh(task)
        return {"task_id": task.id, "status": task.status, "cancelled": True}

    async def _owned_task(self, *, user_id: str, task_id: str) -> Task:
        task = await self.session.scalar(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task

    async def _recent_events(self, *, task_id: str) -> list[TaskEvent]:
        count = int(
            await self.session.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == task_id))
            or 0
        )
        offset = max(count - self.max_events, 0)
        result = await self.session.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.sequence.asc())
            .offset(offset)
            .limit(self.max_events)
        )
        return list(result)

    async def _tool_log(
        self,
        *,
        task_id: str,
        name: str,
        status: str,
        output: dict[str, object],
    ) -> None:
        self.session.add(
            ToolLog(
                task_id=task_id,
                tool_name=name,
                status=status,
                output_text=json.dumps(output, ensure_ascii=False, default=str),
            )
        )
        await self.session.flush()

    @staticmethod
    def _progress(status: str) -> float:
        return {TaskStatus.PENDING.value: 0.0, TaskStatus.RUNNING.value: 0.5, TaskStatus.WAITING_APPROVAL.value: 0.5, TaskStatus.SUCCESS.value: 1.0, TaskStatus.FAILED.value: 1.0, TaskStatus.CANCELLED.value: 1.0}.get(status, 0.0)


def build_task_tool_descriptors(*, enabled: bool = True) -> tuple[ToolDescriptor, ...]:
    return tuple(
        ToolDescriptor(
            name=name,
            description=description,
            input_schema=schema,
            source_id="builtin",
            source_kind="builtin",
            version=TASK_TOOL_VERSION,
            enabled=enabled,
            risk_level=cast(ToolRiskLevel, risk),
            requires_approval=risk != "L1",
            tags=("task", "background", "v10"),
            parallel_safe=risk == "L1",
        )
        for name, description, risk, schema in _TASK_TOOL_DEFS
    )


def build_task_tool_specs(service: AgentTaskToolService) -> tuple[ToolSpec, ...]:
    async def start(invocation: ToolInvocation) -> Any:
        return await service.start_background(
            user_id=invocation.user_id,
            conversation_id=str(invocation.arguments.get("conversation_id") or ""),
            task_type=str(invocation.arguments.get("task_type") or "agent"),
            input_text=str(invocation.arguments.get("input_text") or ""),
            workflow_key=_optional_str(invocation.arguments.get("workflow_key")),
            model_class=_optional_str(invocation.arguments.get("model_class")),
        )

    async def check(invocation: ToolInvocation) -> Any:
        return await service.check_status(user_id=invocation.user_id, task_id=str(invocation.arguments.get("task_id") or ""))

    async def result(invocation: ToolInvocation) -> Any:
        return await service.get_result(user_id=invocation.user_id, task_id=str(invocation.arguments.get("task_id") or ""))

    async def cancel(invocation: ToolInvocation) -> Any:
        return await service.cancel(user_id=invocation.user_id, task_id=str(invocation.arguments.get("task_id") or ""), reason=str(invocation.arguments.get("reason") or "cancelled by user"))

    handlers = {"task.start_background": start, "task.check_status": check, "task.get_result": result, "task.cancel": cancel}
    return tuple(
        ToolSpec(name=name, description=description, risk_level=cast(ToolRiskLevel, risk), handler=handlers[name], input_schema=schema, version=TASK_TOOL_VERSION, source_id="builtin")  # type: ignore[arg-type]
        for name, description, risk, schema in _TASK_TOOL_DEFS
    )


def _optional_str(value: object) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value)


def _truncate(value: str, limit: int) -> str:
    safe = sanitize_text(value)
    if len(safe) <= limit:
        return safe
    return f"{safe[:limit]}..."


_TASK_ID_SCHEMA = {"type": "string", "minLength": 1}
_TASK_TOOL_DEFS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    (
        "task.start_background",
        "Create a governed user-owned background task",
        "L2",
        {"type": "object", "properties": {"conversation_id": _TASK_ID_SCHEMA, "task_type": {"type": "string"}, "input_text": {"type": "string"}, "workflow_key": {"type": "string"}, "model_class": {"type": "string"}}, "required": ["conversation_id", "input_text"], "additionalProperties": False},
    ),
    (
        "task.check_status",
        "Read bounded status and recent events for an owned task",
        "L1",
        {"type": "object", "properties": {"task_id": _TASK_ID_SCHEMA}, "required": ["task_id"], "additionalProperties": False},
    ),
    (
        "task.get_result",
        "Read bounded terminal result summary for an owned task",
        "L1",
        {"type": "object", "properties": {"task_id": _TASK_ID_SCHEMA}, "required": ["task_id"], "additionalProperties": False},
    ),
    (
        "task.cancel",
        "Cancel an owned pending, running, or waiting task",
        "L2",
        {"type": "object", "properties": {"task_id": _TASK_ID_SCHEMA, "reason": {"type": "string"}}, "required": ["task_id"], "additionalProperties": False},
    ),
)
