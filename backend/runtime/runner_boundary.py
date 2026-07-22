from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent.modeling.executors import AgentExecutorProtocol, AgentRunInput
from agent.planning.context import TaskContext
from agent.planning.planner import ExecutionPlan
from agent.ports import ExecutionTracePort
from domain.policies.redaction import sanitize_text
from runtime.runner_events import truncate
from runtime.runner_types import (
    LANGGRAPH_EXECUTOR_TOOL_NAME,
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCESS,
    TASK_STATUS_WAITING_APPROVAL,
    ExecutionOutcome,
)


class ExecutionBoundary:
    """Runs the configured executor inside trace and tool-policy boundaries."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        langgraph_executor: AgentExecutorProtocol,
        sensitive_values: list[str | None] | tuple[str | None, ...] = (),
        trace: ExecutionTracePort | None = None,
    ) -> None:
        self.session = session
        self.langgraph_executor = langgraph_executor
        self.sensitive_values = tuple(sensitive_values)
        self.trace = trace

    async def execute(
        self,
        *,
        task: Any,
        user: Any,
        plan: ExecutionPlan,
        context: TaskContext,
    ) -> ExecutionOutcome:
        input_summary = self._safe_json(
            {
                "plan": asdict(plan),
                "context": asdict(context),
            }
        )
        try:
            result = await self.langgraph_executor.execute(
                run_input=AgentRunInput(plan=plan, context=context)
            )
        except Exception as exc:
            safe_error = self._safe_error(exc)
            await self._record_trace(
                task_id=context.task_id,
                status="failed",
                input_text=input_summary,
                output_text=None,
                error_message=safe_error,
            )
            return ExecutionOutcome(
                status=TASK_STATUS_FAILED,
                error_message=safe_error,
                workflow_key=plan.workflow_key,
            )

        approval_requests = tuple(getattr(result, "approval_requests", ()))
        policy_outcome = tool_policy_outcome(
            plan,
            result.requested_tools,
            approval_requests=approval_requests,
        )
        if policy_outcome is not None:
            payload = {
                "message": policy_outcome.result_text or policy_outcome.error_message,
                "requested_tools": list(result.requested_tools),
                "approval_requests": [asdict(request) for request in approval_requests],
            }
            await self._record_trace(
                task_id=context.task_id,
                status=policy_outcome.status,
                input_text=input_summary,
                output_text=(
                    self._safe_json(payload)
                    if policy_outcome.status == TASK_STATUS_WAITING_APPROVAL
                    else None
                ),
                error_message=(
                    None
                    if policy_outcome.status == TASK_STATUS_WAITING_APPROVAL
                    else self._safe_error(policy_outcome.error_message or "执行失败")
                ),
            )
            return policy_outcome

        metadata = {
            "display_plan": list(result.display_plan),
            "tool_calls": list(result.tool_calls),
            "requested_tools": list(result.requested_tools),
            "approval_requests": [asdict(request) for request in approval_requests],
            "loop_steps": result.loop_steps,
            "checkpoint_id": result.checkpoint_id,
        }
        await self._record_trace(
            task_id=context.task_id,
            status="succeeded",
            input_text=input_summary,
            output_text=self._safe_json(
                {
                    **metadata,
                    "result_text": truncate(result.result_text),
                }
            ),
            error_message=None,
        )
        return ExecutionOutcome(
            status=TASK_STATUS_SUCCESS,
            result_text=result.result_text,
            metadata=metadata,
            workflow_key=plan.workflow_key,
        )

    async def _record_trace(
        self,
        *,
        task_id: str,
        status: str,
        input_text: str,
        output_text: str | None,
        error_message: str | None,
    ) -> None:
        if self.trace is not None:
            await self.trace.record_trace(
                task_id=task_id,
                tool_name=LANGGRAPH_EXECUTOR_TOOL_NAME,
                status=status,
                input_text=input_text,
                output_text=output_text,
                error_message=error_message,
            )
            return

        from runtime.compat import record_execution_trace

        await record_execution_trace(
            self.session,
            task_id=task_id,
            tool_name=LANGGRAPH_EXECUTOR_TOOL_NAME,
            status=status,
            input_text=input_text,
            output_text=output_text,
            error_message=error_message,
        )

    def _safe_error(self, value: object) -> str:
        return sanitize_text(value, extra_sensitive_values=self.sensitive_values)

    def _safe_json(self, payload: dict[str, Any]) -> str:
        return sanitize_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ),
            extra_sensitive_values=self.sensitive_values,
        )


def tool_policy_outcome(
    plan: ExecutionPlan,
    requested_tools: tuple[str, ...],
    *,
    approval_requests: tuple[Any, ...] = (),
) -> ExecutionOutcome | None:
    requested = tuple(dict.fromkeys(tool for tool in requested_tools if tool))
    unauthorized = [
        tool
        for tool in requested
        if tool not in plan.allowed_tools and tool not in plan.approval_required_tools
    ]
    if unauthorized:
        joined = ", ".join(unauthorized)
        return ExecutionOutcome(
            status=TASK_STATUS_FAILED,
            error_message=f"执行计划未授权工具：{joined}。",
            metadata={"requested_tools": list(requested)},
            workflow_key=plan.workflow_key,
        )

    if approval_requests:
        return ExecutionOutcome(
            status=TASK_STATUS_WAITING_APPROVAL,
            result_text="任务需要人工审批后才能继续。",
            metadata={
                "requested_tools": list(requested),
                "approval_requests": [asdict(request) for request in approval_requests],
            },
            workflow_key=plan.workflow_key,
        )
    if not requested:
        return None

    gated = [tool for tool in requested if tool in plan.approval_required_tools]
    if gated:
        joined = ", ".join(gated)
        return ExecutionOutcome(
            status=TASK_STATUS_WAITING_APPROVAL,
            result_text=f"任务需要审批后才能继续：{joined}。",
            metadata={"requested_tools": list(requested)},
            workflow_key=plan.workflow_key,
        )

    return None


__all__ = ["ExecutionBoundary", "tool_policy_outcome"]
