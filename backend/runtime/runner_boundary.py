from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent.ports import AgentExecutorProtocol, AgentRunInput
from agent.planning.context import TaskContext
from agent.planning.planner import ExecutionPlan
from agent.ports import ExecutionTracePort
from domain.policies.redaction import sanitize_text
from domain.policies.enterprise import GovernedAgentProfile, GovernanceValidationError
from runtime.runner_events import truncate
from runtime.runner_types import (
    LANGGRAPH_EXECUTOR_TOOL_NAME,
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCESS,
    TASK_STATUS_WAITING_APPROVAL,
    ExecutionOutcome,
)


class ExecutionBoundary:
    """定义当前组件的职责和边界。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        langgraph_executor: AgentExecutorProtocol,
        sensitive_values: list[str | None] | tuple[str | None, ...] = (),
        trace: ExecutionTracePort | None = None,
    ) -> None:
        """初始化 Agent 执行边界及工具策略、审计和模型依赖。

        Args:
            session: 当前数据库异步会话。
            langgraph_executor: 用于执行当前操作的 langgraph executor 参数。
            sensitive_values: 用于执行当前操作的 sensitive values 参数。
            trace: 用于执行当前操作的 trace 参数。
        """
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
        """在统一边界内执行一次 Agent 运行并返回治理后的结果。

        Args:
            task: 需要处理的任务对象。
            user: 用于执行当前操作的 user 参数。
            plan: 用于执行当前操作的 plan 参数。
            context: 用于执行当前操作的 context 参数。
        """
        input_summary = self._safe_json(
            {
                "plan": asdict(plan),
                "context": asdict(context),
            }
        )
        try:
            governed_profile = self._governed_profile(task)
            result = await self.langgraph_executor.execute(
                run_input=AgentRunInput(
                    plan=plan,
                    context=context,
                    governed_profile=governed_profile,
                )
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

    @staticmethod
    def _governed_profile(task: Any) -> GovernedAgentProfile | None:
        """Load the immutable task snapshot without querying governance storage.

        Args:
            task: 需要处理的任务对象。
        """
        snapshot = getattr(task, "agent_profile_snapshot", None)
        if snapshot is None:
            return None
        if not isinstance(snapshot, str):
            raise GovernanceValidationError("Invalid governed profile snapshot")
        return GovernedAgentProfile.from_snapshot(snapshot)

    async def _record_trace(
        self,
        *,
        task_id: str,
        status: str,
        input_text: str,
        output_text: str | None,
        error_message: str | None,
    ) -> None:
        """记录经过脱敏和截断的运行轨迹，避免泄露完整输入输出。

        Args:
            task_id: 目标任务 ID。
            status: 目标状态。
            input_text: 用于执行当前操作的 input text 参数。
            output_text: 用于执行当前操作的 output text 参数。
            error_message: 用于执行当前操作的 error message 参数。
        """
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
        """把异常转换为可返回给调用方的脱敏错误信息。

        Args:
            value: 待校验、归一化或转换的输入值。
        """
        return sanitize_text(value, extra_sensitive_values=self.sensitive_values)

    def _safe_json(self, payload: dict[str, Any]) -> str:
        """将任意值转换为可安全写入审计事件的 JSON 结构。

        Args:
            payload: 当前操作的结构化载荷。
        """
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
    """检查计划中的工具调用是否符合当前能力和审批策略。

    Args:
        plan: 用于执行当前操作的 plan 参数。
        requested_tools: 用于执行当前操作的 requested tools 参数。
        approval_requests: 用于执行当前操作的 approval requests 参数。
    """
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
