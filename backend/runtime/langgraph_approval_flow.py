from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.types import interrupt
from sqlalchemy import select

from domain.policies.redaction import sanitize_text
from domain.models import Approval, ApprovalStatus, Task
from domain.policies.tool_approval import (
    EXACT_APPROVAL_TOOLS,
    external_approval_binding,
)

from runtime.langgraph_payloads import _work_plan_summary
from runtime.langgraph_state import _ExecutionState

if TYPE_CHECKING:
    from agent.modeling.executors import AgentRunInput
    from runtime.loop import ControlledLoop


class ApprovalFlowMixin:
    """Handles tool, plan, and review approval graph nodes."""

    async def _approval(
        self: Any,
        state: _ExecutionState,
        _run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 approval 的内部辅助逻辑。

        Args:
            state: state 参数。
            _run_input: _run_input 参数。
            loop: loop 参数。
        """
        if self.checkpointer is None:
            raise RuntimeError("Approval interrupt requires a checkpointer")

        async def wait_for_approval() -> _ExecutionState:
            """处理 wait for approval。"""
            decision = state.get("decision", {})
            tool_name = decision.get("tool_name")
            arguments = decision.get("arguments")
            if not isinstance(tool_name, str) or not isinstance(arguments, dict):
                raise RuntimeError("Approval tool decision is unavailable")
            binding = (
                external_approval_binding(tool_name, arguments)
                if tool_name in EXACT_APPROVAL_TOOLS
                else None
            )
            interrupt(
                {
                    "type": "tool_approval",
                    "tool_name": tool_name,
                    "approval_type": "tool",
                    "subject": binding.subject if binding else tool_name,
                    "summary": binding.summary if binding else f"工具调用：{tool_name}",
                }
            )
            return {"requested_tools": []}

        update = await self._run_observed_step(
            "approval",
            _run_input,
            lambda: loop.run_interruptible_step(
                "approval",
                wait_for_approval,
            ),
        )
        update["step_count"] = loop.steps_executed
        return update

    async def _plan_approval(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 plan approval 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """
        if self.checkpointer is None:
            raise RuntimeError("Plan approval interrupt requires a checkpointer")

        async def wait_for_approval() -> _ExecutionState:
            """处理 wait for approval。"""
            subject = f"plan:{state.get('replan_count', 0)}"
            summary = sanitize_text(
                _work_plan_summary(state),
                extra_sensitive_values=self.sensitive_values,
            )[:1000]
            interrupt(
                {
                    "type": "plan_approval",
                    "approval_type": "plan",
                    "subject": subject,
                    "summary": summary,
                }
            )
            if not await self._is_human_approved(
                run_input,
                approval_type="plan",
                subject=subject,
            ):
                raise RuntimeError("Missing exact human approval for plan gate")
            return {}

        update = await self._run_observed_step(
            "plan_approval",
            run_input,
            lambda: loop.run_interruptible_step(
                "plan_approval",
                wait_for_approval,
            ),
        )
        update["step_count"] = loop.steps_executed
        return update

    async def _human_review(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 human review 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """
        if self.checkpointer is None:
            raise RuntimeError("Human review interrupt requires a checkpointer")

        async def wait_for_review() -> _ExecutionState:
            """处理 wait for review。"""
            subject = (
                f"review:{state.get('review_retry_count', 0)}:"
                f"{state.get('replan_count', 0)}"
            )
            feedback = state.get("review_feedback", "需要人工复核")
            candidate = state.get("candidate_result", "")
            summary = sanitize_text(
                f"{feedback}；候选答案：{candidate}",
                extra_sensitive_values=self.sensitive_values,
            )[:1000]
            interrupt(
                {
                    "type": "review_approval",
                    "approval_type": "review",
                    "subject": subject,
                    "summary": summary,
                }
            )
            if not await self._is_human_approved(
                run_input,
                approval_type="review",
                subject=subject,
            ):
                raise RuntimeError("Missing exact human approval for review gate")
            return {}

        update = await self._run_observed_step(
            "human_review",
            run_input,
            lambda: loop.run_interruptible_step(
                "human_review",
                wait_for_review,
            ),
        )
        update["step_count"] = loop.steps_executed
        return update

    async def _is_human_approved(
        self: Any,
        run_input: AgentRunInput,
        *,
        approval_type: str,
        subject: str,
    ) -> bool:
        """执行 处理 is human approved 的内部辅助逻辑。

        Args:
            run_input: run_input 参数。
            approval_type: approval_type 参数。
            subject: subject 参数。
        """
        approval_id = await self.session.scalar(
            select(Approval.id)
            .join(Task, Task.id == Approval.task_id)
            .where(
                Approval.task_id == run_input.context.task_id,
                Approval.approval_type == approval_type,
                Approval.subject == subject,
                Approval.status == ApprovalStatus.APPROVED.value,
                Task.user_id == run_input.context.user_id,
            )
            .limit(1)
        )
        return approval_id is not None
