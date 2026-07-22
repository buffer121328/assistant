from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from langgraph.types import Command

from infrastructure.telemetry.observability import NoopObservability
from runtime.budget import STOP_REASON_DEADLINE, BudgetExceededError, RunBudget
from runtime.loop import ControlledLoop

from runtime.langgraph_approval_flow import ApprovalFlowMixin
from runtime.langgraph_graph import GraphBuilderMixin
from runtime.langgraph_model_flow import ModelFlowMixin
from runtime.langgraph_payloads import _approval_requests_from_interrupts
from runtime.langgraph_review_flow import ReviewFlowMixin
from runtime.langgraph_runtime_helpers import RuntimeHelperMixin
from runtime.langgraph_state import _AGENT_CORE_VERSION, _ExecutionState
from runtime.langgraph_tool_flow import ToolFlowMixin

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from sqlalchemy.ext.asyncio import AsyncSession

    from agent.modeling.agent_model import AgentModelProtocol
    from agent.modeling.executors import AgentRunInput, AgentRunResult
    from infrastructure.telemetry.observability import Observability
    from runtime.subagents import SubAgentCoordinator
    from tools.core.catalog import ToolCatalogSnapshot
    from tools.core.registry import ToolRegistry


class LangGraphExecutor(
    ApprovalFlowMixin,
    GraphBuilderMixin,
    ModelFlowMixin,
    ReviewFlowMixin,
    RuntimeHelperMixin,
    ToolFlowMixin,
):
    """表示 处理 lang graph executor 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        tool_registry: ToolRegistry,
        model: AgentModelProtocol,
        checkpointer: BaseCheckpointSaver | None,
        sensitive_values: tuple[str | None, ...] = (),
        tool_snapshot: ToolCatalogSnapshot | None = None,
        observability: Observability | None = None,
        subagent_coordinator: SubAgentCoordinator | None = None,
        prompt_builder: Any | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            tool_registry: tool_registry 参数。
            model: model 参数。
            checkpointer: checkpointer 参数。
            sensitive_values: sensitive_values 参数。
            tool_snapshot: tool_snapshot 参数。
            observability: observability 参数。
            subagent_coordinator: subagent_coordinator 参数。
            prompt_builder: prompt_builder 参数。
        """
        self.session = session
        self.tool_registry = tool_registry
        self.model = model
        self.checkpointer = checkpointer
        self.sensitive_values = sensitive_values
        self.tool_snapshot = tool_snapshot
        self.observability = observability or NoopObservability()
        self.subagent_coordinator = subagent_coordinator
        self.prompt_builder = prompt_builder

    async def execute(self, *, run_input: AgentRunInput) -> AgentRunResult:
        """执行。

        Args:
            run_input: run_input 参数。
        """
        from agent.modeling.executors import AgentRunResult

        deadline_at = datetime.now(UTC) + timedelta(
            seconds=run_input.plan.timeout_seconds
        )
        budget = RunBudget.from_limits(
            max_steps=run_input.plan.max_steps,
            max_tool_calls=run_input.plan.tool_count_budget,
            deadline_at=deadline_at,
        )
        if hasattr(self.model, "set_run_budget"):
            self.model.set_run_budget(budget)
        loop = ControlledLoop(
            session=self.session,
            task_id=run_input.context.task_id,
            max_steps=run_input.plan.max_steps,
            sensitive_values=self.sensitive_values,
            budget=budget,
            now=lambda: datetime.now(UTC),
        )
        graph = self._build_graph(run_input, loop)
        config: dict[str, Any] = {
            "configurable": {
                "thread_id": run_input.context.task_id,
            },
            "metadata": {"agent_core_version": _AGENT_CORE_VERSION},
            "recursion_limit": max(run_input.plan.max_steps + 4, 8),
        }
        initial_state: _ExecutionState = {
            "tool_schemas": [],
            "history": [],
            "display_plan": [],
            "sources": [],
            "tool_calls": [],
            "requested_tools": [],
            "review_retry_count": 0,
            "replan_count": 0,
            "step_count": 0,
        }
        previous_snapshot = await self._snapshot(graph, config)
        if previous_snapshot is not None and previous_snapshot.interrupts:
            previous_values = cast(_ExecutionState, previous_snapshot.values)
            loop.steps_executed = int(previous_values.get("step_count", 0))
            graph_input: _ExecutionState | Command = Command(resume=True)
        else:
            graph_input = initial_state
        try:
            async with asyncio.timeout(run_input.plan.timeout_seconds):
                final_state = cast(
                    _ExecutionState,
                    await graph.ainvoke(graph_input, config=config),
                )
        except BudgetExceededError as exc:
            if exc.stop_reason == STOP_REASON_DEADLINE:
                raise TimeoutError("Agent execution deadline exceeded") from exc
            raise

        snapshot = await self._snapshot(graph, config)
        checkpoint_id = self._checkpoint_id(snapshot)
        if snapshot is not None and snapshot.interrupts:
            interrupted_state = cast(_ExecutionState, snapshot.values)
            approval_requests = _approval_requests_from_interrupts(snapshot.interrupts)
            requested_tools = tuple(
                request.tool_name or request.subject
                for request in approval_requests
                if request.approval_type == "tool"
            )
            return AgentRunResult(
                result_text="任务需要人工审批后才能继续。",
                display_plan=tuple(interrupted_state.get("display_plan", [])),
                tool_calls=tuple(interrupted_state.get("tool_calls", [])),
                requested_tools=requested_tools,
                loop_steps=loop.steps_executed,
                checkpoint_id=checkpoint_id,
                approval_requests=approval_requests,
            )

        result_text = final_state.get("result_text")
        if not result_text:
            raise RuntimeError("Agent graph ended without a final answer")
        return AgentRunResult(
            result_text=result_text,
            display_plan=tuple(final_state.get("display_plan", [])),
            tool_calls=tuple(final_state.get("tool_calls", [])),
            requested_tools=tuple(final_state.get("requested_tools", [])),
            loop_steps=loop.steps_executed,
            checkpoint_id=checkpoint_id,
        )
