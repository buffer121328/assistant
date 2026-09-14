from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
import json
from typing import Any, TypeVar

from langgraph.errors import GraphInterrupt
from sqlalchemy.ext.asyncio import AsyncSession

from runtime.budget import BudgetExceededError, RunBudget
from domain.models import ToolLog
from domain.policies.redaction import sanitize_text


T = TypeVar("T")


class LoopStepLimitError(Exception):
    """表示 处理 loop step limit error 的后端数据结构或服务对象。"""

    pass


class ControlledLoop:
    """表示 处理 controlled loop 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        task_id: str,
        max_steps: int,
        sensitive_values: Iterable[str | None] = (),
        budget: RunBudget | None = None,
        now: Callable[[], datetime | None] | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            task_id: task_id 参数。
            max_steps: max_steps 参数。
            sensitive_values: sensitive_values 参数。
            budget: budget 参数。
            now: now 参数。
        """
        self.session = session
        self.task_id = task_id
        self.max_steps = max_steps
        self.sensitive_values = tuple(sensitive_values)
        self.steps_executed = 0
        self.budget = budget
        self.now = now or (lambda: None)

    async def run_step(
        self,
        name: str,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        """运行 step。

        Args:
            name: name 参数。
            operation: operation 参数。
        """
        if self.budget is not None:
            try:
                self.budget.consume_step(now=self.now())
            except BudgetExceededError as exc:
                await self._record_budget_rejection(name=name, error=exc)
                if exc.stop_reason == "step_limit_exceeded":
                    raise LoopStepLimitError(str(exc)) from exc
                raise
        elif self.steps_executed >= self.max_steps:
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
        """运行 interruptible step。

        Args:
            name: name 参数。
            operation: operation 参数。
        """
        if self.budget is not None:
            try:
                self.budget.consume_step(now=self.now())
            except BudgetExceededError as exc:
                await self._record_budget_rejection(name=name, error=exc)
                if exc.stop_reason == "step_limit_exceeded":
                    raise LoopStepLimitError(str(exc)) from exc
                raise
        elif self.steps_executed >= self.max_steps:
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
                output_text=self._safe_json({"step": name, "interrupted": True}),
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

    async def _record_budget_rejection(
        self, *, name: str, error: BudgetExceededError
    ) -> None:
        """执行 记录 budget rejection 的内部辅助逻辑。

        Args:
            name: name 参数。
            error: error 参数。
        """
        await self._record(
            name=name,
            status="failed",
            input_text=self._safe_json({"step": name, "budget": error.summary}),
            output_text=None,
            error_message=self._safe_json(
                {"stop_reason": error.stop_reason, "budget": error.summary}
            ),
        )

    async def _record(
        self,
        *,
        name: str,
        status: str,
        input_text: str,
        output_text: str | None,
        error_message: str | None,
    ) -> None:
        """执行 记录 的内部辅助逻辑。

        Args:
            name: name 参数。
            status: status 参数。
            input_text: input_text 参数。
            output_text: output_text 参数。
            error_message: error_message 参数。
        """
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
        """执行 处理 safe json 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
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
        """执行 处理 safe text 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        text = sanitize_text(value, extra_sensitive_values=self.sensitive_values)
        if "traceback" in text.lower():
            return "内部错误已脱敏"
        return text
