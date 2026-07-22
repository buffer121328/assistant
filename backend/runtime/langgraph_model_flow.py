from __future__ import annotations

from typing import TYPE_CHECKING, Any

from runtime.subagents import SubAgentRequest

from runtime.langgraph_payloads import (
    _decision_payload,
    _work_plan_from_state,
    _work_plan_payload,
)
from runtime.langgraph_state import _ExecutionState

if TYPE_CHECKING:
    from agent.modeling.executors import AgentRunInput
    from runtime.loop import ControlledLoop


class ModelFlowMixin:
    """Handles prepare, model, planning, and subagent graph nodes."""

    async def _prepare(
        self: Any,
        _state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 准备 的内部辅助逻辑。

        Args:
            _state: _state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """

        async def prepare() -> _ExecutionState:
            """准备。"""
            return {"tool_schemas": list(self.planned_tool_schemas(run_input))}

        update = await self._run_observed_step(
            "prepare",
            run_input,
            lambda: loop.run_step("prepare", prepare),
        )
        update["step_count"] = loop.steps_executed
        return update

    async def _model(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 model 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """

        async def decide() -> _ExecutionState:
            """处理 decide。"""
            from agent.modeling.agent_model import build_agent_model_request

            request = build_agent_model_request(
                run_input,
                tool_schemas=tuple(state.get("tool_schemas", [])),
                history=tuple(state.get("history", [])),
                work_plan=_work_plan_from_state(state),
                sensitive_values=self.sensitive_values,
                prompt_builder=self.prompt_builder,
            )
            decision = await self.model.decide(request)
            update: _ExecutionState = {
                "decision": _decision_payload(decision),
            }
            if run_input.plan.execution_mode == "react":
                update["display_plan"] = list(decision.plan)
            if decision.action == "final":
                if run_input.plan.execution_mode == "plan_execute_review":
                    update["candidate_result"] = decision.answer or ""
                else:
                    update["result_text"] = decision.answer or ""
            return update

        update = await self._run_observed_step(
            "model",
            run_input,
            lambda: loop.run_step("model", decide),
        )
        update["step_count"] = loop.steps_executed
        return update

    def _route_after_model(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
    ) -> str:
        """执行 路由 after model 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
        """
        decision = state.get("decision", {})
        action = decision.get("action")
        if action == "tool_batch":
            return "tool_batch"
        if action != "tool_call":
            if run_input.plan.execution_mode == "plan_execute_review":
                return "review"
            return "final"
        tool_name = decision.get("tool_name")
        if tool_name in run_input.plan.approval_required_tools:
            return "approval"
        return "tool"

    def _should_delegate(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
    ) -> bool:
        """执行 处理 should delegate 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
        """
        if self.subagent_coordinator is None or run_input.plan.max_subagents <= 0:
            return False
        work_plan = _work_plan_from_state(state)
        return bool(
            work_plan and any(step.agent_role is not None for step in work_plan.steps)
        )

    async def _subagents(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 subagents 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """

        async def delegate() -> _ExecutionState:
            """处理 delegate。"""
            work_plan = _work_plan_from_state(state)
            coordinator = self.subagent_coordinator
            if work_plan is None or coordinator is None:
                return {}
            requests = tuple(
                SubAgentRequest(
                    step_index=index,
                    role=step.agent_role,
                    objective=step.objective,
                    context=(
                        f"goal={work_plan.goal}\n"
                        f"input={run_input.context.input_text}\n"
                        f"memory={run_input.context.memory_summary}"
                    ),
                )
                for index, step in enumerate(work_plan.steps)
                if step.agent_role is not None
            )[: run_input.plan.max_subagents]
            results = await coordinator.run(
                task_id=run_input.context.task_id,
                user_id=run_input.context.user_id,
                requests=requests,
            )
            history = list(state.get("history", []))
            payloads: list[dict[str, Any]] = []
            for result in results:
                payload = {
                    "step_index": result.step_index,
                    "role": result.role,
                    "content": result.content,
                    "error": result.error,
                }
                payloads.append(payload)
                history.append(
                    {
                        "role": "subagent",
                        "name": f"subagent.{result.role}",
                        "content": self._safe_json(payload),
                    }
                )
            return {"history": history, "subagent_results": payloads}

        update = await self._run_observed_step(
            "subagents",
            run_input,
            lambda: loop.run_step("subagents", delegate),
        )
        update["step_count"] = loop.steps_executed
        return update

    async def _plan(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 plan 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """

        async def create_plan() -> _ExecutionState:
            """创建 plan。"""
            from agent.modeling.agent_model import build_work_plan_request

            request = build_work_plan_request(
                run_input,
                sensitive_values=self.sensitive_values,
            )
            work_plan = await self.model.create_plan(request)
            return {
                "work_plan": _work_plan_payload(work_plan),
                "display_plan": [step.objective for step in work_plan.steps],
                "candidate_result": "",
                "decision": {},
            }

        update = await self._run_observed_step(
            "plan",
            run_input,
            lambda: loop.run_step("plan", create_plan),
        )
        update["step_count"] = loop.steps_executed
        return update
