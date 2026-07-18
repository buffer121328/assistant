from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
import json
from typing import Any, TypeVar

from langgraph.errors import GraphInterrupt
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import ToolLog
from model_gateway import sanitize_text


T = TypeVar("T")


class LoopStepLimitError(Exception):
    pass


class ControlledLoop:
    def __init__(
        self,
        *,
        session: AsyncSession,
        task_id: str,
        max_steps: int,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        self.session = session
        self.task_id = task_id
        self.max_steps = max_steps
        self.sensitive_values = tuple(sensitive_values)
        self.steps_executed = 0

    async def run_step(
        self,
        name: str,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        if self.steps_executed >= self.max_steps:
            raise LoopStepLimitError(
                f"Execution plan step limit reached: {self.max_steps}"
            )

        self.steps_executed += 1
        input_text = self._safe_json(
            {
                "step": name,
                "step_index": self.steps_executed,
                "max_steps": self.max_steps,
            }
        )
        try:
            result = await operation()
        except Exception as exc:
            await self._record(
                name=name,
                status="failed",
                input_text=input_text,
                output_text=None,
                error_message=self._safe_text(exc),
            )
            raise

        await self._record(
            name=name,
            status="succeeded",
            input_text=input_text,
            output_text=self._safe_json({"step": name, "completed": True}),
            error_message=None,
        )
        return result

    async def run_interruptible_step(
        self,
        name: str,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        if self.steps_executed >= self.max_steps:
            raise LoopStepLimitError(
                f"Execution plan step limit reached: {self.max_steps}"
            )

        self.steps_executed += 1
        input_text = self._safe_json(
            {
                "step": name,
                "step_index": self.steps_executed,
                "max_steps": self.max_steps,
            }
        )
        try:
            result = await operation()
        except GraphInterrupt:
            await self._record(
                name=name,
                status="waiting_approval",
                input_text=input_text,
                output_text=self._safe_json(
                    {"step": name, "interrupted": True}
                ),
                error_message=None,
            )
            raise
        except Exception as exc:
            await self._record(
                name=name,
                status="failed",
                input_text=input_text,
                output_text=None,
                error_message=self._safe_text(exc),
            )
            raise

        await self._record(
            name=name,
            status="succeeded",
            input_text=input_text,
            output_text=self._safe_json({"step": name, "completed": True}),
            error_message=None,
        )
        return result

    async def _record(
        self,
        *,
        name: str,
        status: str,
        input_text: str,
        output_text: str | None,
        error_message: str | None,
    ) -> None:
        self.session.add(
            ToolLog(
                task_id=self.task_id,
                tool_name=f"langgraph.step.{name}",
                status=status,
                input_text=input_text,
                output_text=output_text,
                error_message=error_message,
            )
        )
        await self.session.flush()

    def _safe_json(self, value: Any) -> str:
        return self._safe_text(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            )
        )

    def _safe_text(self, value: object) -> str:
        text = sanitize_text(value, extra_sensitive_values=self.sensitive_values)
        if "traceback" in text.lower():
            return "内部错误已脱敏"
        return text
