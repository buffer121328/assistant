from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.policies.tool_approval import EXACT_APPROVAL_TOOLS, external_audit_arguments

from runtime.langgraph_state import _ExecutionState

if TYPE_CHECKING:
    from agent.modeling.executors import AgentRunInput
    from runtime.loop import ControlledLoop


class ToolFlowMixin:
    """Handles tool execution and planned tool schema validation."""

    async def _tool(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 tool 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """

        async def call_tool() -> _ExecutionState:
            """处理 call tool。"""
            from tools.builtin.search import SearchWebResult
            from tools.core.registry import ToolInvocation

            decision = state.get("decision", {})
            tool_name = decision.get("tool_name")
            arguments = decision.get("arguments")
            if not isinstance(tool_name, str) or not isinstance(arguments, dict):
                raise RuntimeError("Agent tool decision is unavailable")
            invocation = ToolInvocation(
                task_id=run_input.context.task_id,
                user_id=run_input.context.user_id,
                name=tool_name,
                arguments=arguments,
                tool_snapshot_revision=(run_input.plan.tool_snapshot_revision or None),
                tool_version=dict(run_input.plan.tool_versions).get(tool_name),
            )
            with self.observability.observe(
                "agent.tool.call",
                as_type="tool",
                input={
                    "tool_name": tool_name,
                    "arguments": (
                        external_audit_arguments(tool_name, arguments)
                        if tool_name in EXACT_APPROVAL_TOOLS
                        else arguments
                    ),
                },
                metadata={
                    "task_id": run_input.context.task_id,
                    "tool_name": tool_name,
                    "tool_snapshot_revision": run_input.plan.tool_snapshot_revision,
                },
            ) as observation:
                result = await self.tool_registry.execute(
                    invocation,
                    allowed_tools=run_input.plan.allowed_tools,
                    approval_required_tools=run_input.plan.approval_required_tools,
                    budget=loop.budget,
                )
                observation.update(output={"status": "success"})
            sources = list(state.get("sources", []))
            if isinstance(result, SearchWebResult):
                sources.extend(result.to_workflow_sources())
            history = list(state.get("history", []))
            history.append(
                {
                    "role": "tool",
                    "name": tool_name,
                    "content": self._safe_json(result),
                }
            )
            return {
                "history": history,
                "sources": sources,
                "tool_calls": [*state.get("tool_calls", []), tool_name],
            }

        update = await self._run_observed_step(
            "tool",
            run_input,
            lambda: loop.run_step("tool", call_tool),
        )
        update["step_count"] = loop.steps_executed
        return update

    async def _tool_batch(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 tool batch 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """

        async def call_tools() -> _ExecutionState:
            """处理 call tools。"""
            from tools.core.registry import ToolInvocation

            decision = state.get("decision", {})
            raw_calls = decision.get("tool_calls")
            if not isinstance(raw_calls, list):
                raise RuntimeError("Agent tool batch decision is unavailable")
            versions = dict(run_input.plan.tool_versions)
            invocations: list[ToolInvocation] = []
            names: list[str] = []
            for item in raw_calls:
                if not isinstance(item, dict):
                    raise RuntimeError("Agent tool batch item is unavailable")
                name = item.get("tool_name")
                arguments = item.get("arguments")
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise RuntimeError("Agent tool batch item is invalid")
                names.append(name)
                invocations.append(
                    ToolInvocation(
                        task_id=run_input.context.task_id,
                        user_id=run_input.context.user_id,
                        name=name,
                        arguments=arguments,
                        tool_snapshot_revision=(
                            run_input.plan.tool_snapshot_revision or None
                        ),
                        tool_version=versions.get(name),
                    )
                )
            results = await self.tool_registry.execute_batch(
                tuple(invocations),
                allowed_tools=run_input.plan.allowed_tools,
                approval_required_tools=run_input.plan.approval_required_tools,
                budget=loop.budget,
            )
            history = list(state.get("history", []))
            for name, result in zip(names, results, strict=True):
                history.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": self._safe_json(result),
                    }
                )
            return {
                "history": history,
                "tool_calls": [*state.get("tool_calls", []), *names],
            }

        update = await self._run_observed_step(
            "tool_batch",
            run_input,
            lambda: loop.run_step("tool_batch", call_tools),
        )
        update["step_count"] = loop.steps_executed
        return update

    def planned_tool_schemas(
        self: Any,
        run_input: AgentRunInput,
    ) -> tuple[dict[str, Any], ...]:
        """处理 planned tool schemas。

        Args:
            run_input: run_input 参数。
        """
        from tools.core.catalog import build_planned_tool_schemas
        from tools.core.registry import ToolSnapshotStaleError

        if self.tool_snapshot is None:
            if run_input.plan.allowed_tools or run_input.plan.approval_required_tools:
                raise ToolSnapshotStaleError("Tool snapshot is unavailable")
            return ()
        if (
            run_input.plan.tool_snapshot_revision
            and run_input.plan.tool_snapshot_revision != self.tool_snapshot.revision
        ):
            raise ToolSnapshotStaleError("Tool snapshot changed before execution")
        schemas = build_planned_tool_schemas(
            self.tool_snapshot,
            allowed_tools=run_input.plan.allowed_tools,
            approval_required_tools=run_input.plan.approval_required_tools,
        )
        planned_names = tuple(
            dict.fromkeys(
                (
                    *run_input.plan.allowed_tools,
                    *run_input.plan.approval_required_tools,
                )
            )
        )
        schema_names = tuple(schema["function"]["name"] for schema in schemas)
        if schema_names != planned_names:
            raise ToolSnapshotStaleError("Planned tool schema is unavailable")
        return schemas
