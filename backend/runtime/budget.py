from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


STOP_REASON_STEP_LIMIT = "step_limit_exceeded"
STOP_REASON_TOOL_LIMIT = "tool_call_limit_exceeded"
STOP_REASON_TOKEN_LIMIT = "token_limit_exceeded"
STOP_REASON_DEADLINE = "deadline_exceeded"


class BudgetExceededError(RuntimeError):
    """表示 处理 budget exceeded error 的后端数据结构或服务对象。"""

    def __init__(self, stop_reason: str, summary: Mapping[str, Any]) -> None:
        """初始化对象实例。

        Args:
            stop_reason: stop_reason 参数。
            summary: summary 参数。
        """
        self.stop_reason = stop_reason
        self.summary = dict(summary)
        super().__init__(f"Run budget exceeded: {stop_reason}")


@dataclass
class RunBudget:
    """表示 运行 budget 的后端数据结构或服务对象。"""

    max_steps: int = 12
    max_tool_calls: int = 15
    max_input_tokens: int = 32_000
    max_output_tokens: int = 8_000
    deadline_at: datetime | None = None
    steps_used: int = 0
    tool_calls_used: int = 0
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    stop_reason: str | None = None

    def check_deadline(self, *, now: datetime | None = None) -> None:
        """检查 deadline。

        Args:
            now: now 参数。
        """
        if self.deadline_at is None or now is None:
            return
        if now >= self.deadline_at:
            self._stop(STOP_REASON_DEADLINE)

    def check_can_continue(self, *, now: datetime | None = None) -> None:
        """检查 can continue。

        Args:
            now: now 参数。
        """
        if self.stop_reason is not None:
            self._raise()
        self.check_deadline(now=now)

    def consume_step(self, *, now: datetime | None = None) -> None:
        """处理 consume step。

        Args:
            now: now 参数。
        """
        self.check_can_continue(now=now)
        if self.steps_used >= self.max_steps:
            self._stop(STOP_REASON_STEP_LIMIT)
        self.steps_used += 1

    def consume_tool_call(self, count: int = 1, *, now: datetime | None = None) -> None:
        """处理 consume tool call。

        Args:
            count: count 参数。
            now: now 参数。
        """
        self.check_can_continue(now=now)
        if count < 1:
            return
        if self.tool_calls_used + count > self.max_tool_calls:
            self._stop(STOP_REASON_TOOL_LIMIT)
        self.tool_calls_used += count

    def record_model_usage(self, *, input_tokens: int, output_tokens: int) -> None:
        """记录 model usage。

        Args:
            input_tokens: input_tokens 参数。
            output_tokens: output_tokens 参数。
        """
        self.check_can_continue()
        self.input_tokens_used += max(0, input_tokens)
        self.output_tokens_used += max(0, output_tokens)
        if (
            self.input_tokens_used > self.max_input_tokens
            or self.output_tokens_used > self.max_output_tokens
        ):
            self._stop(STOP_REASON_TOKEN_LIMIT)

    def summary(self) -> dict[str, Any]:
        """处理 summary。"""
        return {
            "limits": {
                "max_steps": self.max_steps,
                "max_tool_calls": self.max_tool_calls,
                "max_input_tokens": self.max_input_tokens,
                "max_output_tokens": self.max_output_tokens,
                "deadline_at": self.deadline_at.isoformat()
                if self.deadline_at
                else None,
            },
            "used": {
                "steps": self.steps_used,
                "tool_calls": self.tool_calls_used,
                "input_tokens": self.input_tokens_used,
                "output_tokens": self.output_tokens_used,
            },
            "remaining": {
                "steps": max(0, self.max_steps - self.steps_used),
                "tool_calls": max(0, self.max_tool_calls - self.tool_calls_used),
                "input_tokens": max(0, self.max_input_tokens - self.input_tokens_used),
                "output_tokens": max(
                    0, self.max_output_tokens - self.output_tokens_used
                ),
            },
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_limits(
        cls,
        *,
        max_steps: int,
        max_tool_calls: int,
        max_input_tokens: int = 32_000,
        max_output_tokens: int = 8_000,
        deadline_at: datetime | None = None,
    ) -> RunBudget:
        """根据输入创建 limits。

        Args:
            max_steps: max_steps 参数。
            max_tool_calls: max_tool_calls 参数。
            max_input_tokens: max_input_tokens 参数。
            max_output_tokens: max_output_tokens 参数。
            deadline_at: deadline_at 参数。
        """
        return cls(
            max_steps=max(0, max_steps),
            max_tool_calls=max(0, max_tool_calls),
            max_input_tokens=max(0, max_input_tokens),
            max_output_tokens=max(0, max_output_tokens),
            deadline_at=deadline_at,
        )

    def _stop(self, stop_reason: str) -> None:
        """执行 停止 的内部辅助逻辑。

        Args:
            stop_reason: stop_reason 参数。
        """
        self.stop_reason = stop_reason
        self._raise()

    def _raise(self) -> None:
        """执行 处理 raise 的内部辅助逻辑。"""
        assert self.stop_reason is not None
        raise BudgetExceededError(self.stop_reason, self.summary())


NowProvider = Callable[[], datetime | None]
