from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from runtime.langgraph_state import _ExecutionState

if TYPE_CHECKING:
    from agent.modeling.executors import AgentRunInput
    from runtime.loop import ControlledLoop


class GraphBuilderMixin:
    """Builds the LangGraph execution graph."""

    def _build_graph(
        self: Any,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> Any:
        """执行 构建 graph 的内部辅助逻辑。

        Args:
            run_input: run_input 参数。
            loop: loop 参数。
        """

        async def prepare(state: _ExecutionState) -> _ExecutionState:
            """准备。

            Args:
                state: state 参数。
            """
            return await self._prepare(state, run_input, loop)

        async def model(state: _ExecutionState) -> _ExecutionState:
            """处理 model。

            Args:
                state: state 参数。
            """
            return await self._model(state, run_input, loop)

        async def plan(state: _ExecutionState) -> _ExecutionState:
            """处理 plan。

            Args:
                state: state 参数。
            """
            return await self._plan(state, run_input, loop)

        async def review(state: _ExecutionState) -> _ExecutionState:
            """处理 review。

            Args:
                state: state 参数。
            """
            return await self._review(state, run_input, loop)

        async def finalize(state: _ExecutionState) -> _ExecutionState:
            """处理 finalize。

            Args:
                state: state 参数。
            """
            return await self._finalize(state, run_input, loop)

        async def fail(state: _ExecutionState) -> _ExecutionState:
            """处理 fail。

            Args:
                state: state 参数。
            """
            return await self._fail_review(state, run_input, loop)

        async def tool(state: _ExecutionState) -> _ExecutionState:
            """处理 tool。

            Args:
                state: state 参数。
            """
            return await self._tool(state, run_input, loop)

        async def tool_batch(state: _ExecutionState) -> _ExecutionState:
            """处理 tool batch。

            Args:
                state: state 参数。
            """
            return await self._tool_batch(state, run_input, loop)

        async def subagents(state: _ExecutionState) -> _ExecutionState:
            """处理 subagents。

            Args:
                state: state 参数。
            """
            return await self._subagents(state, run_input, loop)

        async def approval(state: _ExecutionState) -> _ExecutionState:
            """处理 approval。

            Args:
                state: state 参数。
            """
            return await self._approval(state, run_input, loop)

        async def plan_approval(state: _ExecutionState) -> _ExecutionState:
            """处理 plan approval。

            Args:
                state: state 参数。
            """
            return await self._plan_approval(state, run_input, loop)

        async def human_review(state: _ExecutionState) -> _ExecutionState:
            """处理 human review。

            Args:
                state: state 参数。
            """
            return await self._human_review(state, run_input, loop)

        def route_after_model(state: _ExecutionState) -> str:
            """路由 after model。

            Args:
                state: state 参数。
            """
            return self._route_after_model(state, run_input)

        def route_after_prepare(_state: _ExecutionState) -> str:
            """路由 after prepare。

            Args:
                _state: _state 参数。
            """
            if run_input.plan.execution_mode == "plan_execute_review":
                return "plan"
            return "model"

        def route_after_plan(state: _ExecutionState) -> str:
            """路由 after plan。

            Args:
                state: state 参数。
            """
            if run_input.plan.require_plan_approval:
                return "approval"
            return "subagents" if self._should_delegate(state, run_input) else "model"

        def route_after_plan_approval(state: _ExecutionState) -> str:
            """路由 after plan approval。

            Args:
                state: state 参数。
            """
            return "subagents" if self._should_delegate(state, run_input) else "model"

        def route_after_review(state: _ExecutionState) -> str:
            """路由 after review。

            Args:
                state: state 参数。
            """
            return self._route_after_review(state)

        graph = StateGraph(_ExecutionState)
        graph.add_node("prepare", prepare)
        graph.add_node("model", model)
        graph.add_node("plan", plan)
        graph.add_node("review", review)
        graph.add_node("finalize", finalize)
        graph.add_node("review_failure", fail)
        graph.add_node("tool", tool)
        graph.add_node("tool_batch", tool_batch)
        graph.add_node("subagents", subagents)
        graph.add_node("approval", approval)
        graph.add_node("plan_approval", plan_approval)
        graph.add_node("human_review", human_review)
        graph.add_edge(START, "prepare")
        graph.add_conditional_edges(
            "prepare",
            route_after_prepare,
            {"plan": "plan", "model": "model"},
        )
        graph.add_conditional_edges(
            "plan",
            route_after_plan,
            {"approval": "plan_approval", "subagents": "subagents", "model": "model"},
        )
        graph.add_conditional_edges(
            "plan_approval",
            route_after_plan_approval,
            {"subagents": "subagents", "model": "model"},
        )
        graph.add_edge("subagents", "model")
        graph.add_conditional_edges(
            "model",
            route_after_model,
            {
                "tool": "tool",
                "tool_batch": "tool_batch",
                "approval": "approval",
                "review": "review",
                "final": END,
            },
        )
        graph.add_edge("approval", "tool")
        graph.add_edge("tool", "model")
        graph.add_edge("tool_batch", "model")
        graph.add_conditional_edges(
            "review",
            route_after_review,
            {
                "finalize": "finalize",
                "retry": "model",
                "replan": "plan",
                "human_review": "human_review",
                "fail": "review_failure",
            },
        )
        graph.add_edge("human_review", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=self.checkpointer)
