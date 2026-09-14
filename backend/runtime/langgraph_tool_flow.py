from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING, Any

from domain.policies.tool_approval import EXACT_APPROVAL_TOOLS, external_audit_arguments

from runtime.langgraph_state import _ExecutionState

if TYPE_CHECKING:
    from agent.ports import AgentRunInput
    from runtime.loop import ControlledLoop


class ToolFlowMixin:
    """定义当前组件的职责和边界。"""

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
                    "user_id": run_input.context.user_id,
                    "agent_run_id": getattr(self, "agent_run_id", None),
                    "tool_name": tool_name,
                    "tool_snapshot_revision": run_input.plan.tool_snapshot_revision,
                    "batch": False,
                },
            ) as observation:
                from tools.gateway import ToolGateway

                result = await ToolGateway(self.tool_registry).execute(
                    invocation,
                    allowed_tools=run_input.plan.allowed_tools,
                    approval_required_tools=run_input.plan.approval_required_tools,
                    budget=loop.budget,
                )
                observation.update(output={"status": "success"})
            content, result_sources = await self._history_content_for_result(
                tool_name=tool_name,
                result=result,
                run_input=run_input,
            )
            sources = [*state.get("sources", []), *result_sources]
            history = list(state.get("history", []))
            history.append(
                {
                    "role": "tool",
                    "name": tool_name,
                    "content": content,
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
            observations = []
            with ExitStack() as stack:
                for invocation in invocations:
                    observations.append(
                        stack.enter_context(
                            self.observability.observe(
                                "agent.tool.call",
                                as_type="tool",
                                input={
                                    "tool_name": invocation.name,
                                    "arguments": (
                                        external_audit_arguments(
                                            invocation.name, invocation.arguments
                                        )
                                        if invocation.name in EXACT_APPROVAL_TOOLS
                                        else invocation.arguments
                                    ),
                                },
                                metadata={
                                    "task_id": run_input.context.task_id,
                                    "user_id": run_input.context.user_id,
                                    "agent_run_id": getattr(self, "agent_run_id", None),
                                    "tool_name": invocation.name,
                                    "tool_snapshot_revision": (
                                        run_input.plan.tool_snapshot_revision
                                    ),
                                    "batch": True,
                                },
                            )
                        )
                    )
                from tools.gateway import ToolGateway

                results = await ToolGateway(self.tool_registry).execute_batch(
                    tuple(invocations),
                    allowed_tools=run_input.plan.allowed_tools,
                    approval_required_tools=run_input.plan.approval_required_tools,
                    budget=loop.budget,
                )
                for observation in observations:
                    observation.update(output={"status": "success"})
            history = list(state.get("history", []))
            sources = list(state.get("sources", []))
            for name, result in zip(names, results, strict=True):
                content, result_sources = await self._history_content_for_result(
                    tool_name=name,
                    result=result,
                    run_input=run_input,
                )
                sources.extend(result_sources)
                history.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": content,
                    }
                )
            return {
                "history": history,
                "sources": sources,
                "tool_calls": [*state.get("tool_calls", []), *names],
            }

        update = await self._run_observed_step(
            "tool_batch",
            run_input,
            lambda: loop.run_step("tool_batch", call_tools),
        )
        update["step_count"] = loop.steps_executed
        return update

    async def _history_content_for_result(
        self: Any,
        *,
        tool_name: str,
        result: Any,
        run_input: AgentRunInput,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Return only content that is safe to add to model history.

        Args:
            tool_name: 用于执行当前操作的 tool name 参数。
            result: 需要处理的执行结果。
            run_input: 用于执行当前操作的 run input 参数。
        """
        from agent.governance.content import (
            ContentGuardDecision,
            ContentGuardRequest,
            ContentSource,
            format_untrusted_source_payload,
        )
        from tools.builtin.search import SearchWebResult

        if not isinstance(result, SearchWebResult) or not self.content_guard.enabled:
            return self._safe_json(result), (
                result.to_workflow_sources()
                if isinstance(result, SearchWebResult)
                else []
            )

        retained_sources: list[dict[str, Any]] = []
        history_sources: list[dict[str, Any]] = []
        for raw_source in result.sources:
            source = ContentSource(
                title=raw_source.title,
                url=raw_source.url,
                snippet=raw_source.snippet,
                provider_metadata=raw_source.provider_metadata,
            )
            try:
                guard_decision = await self.content_guard.inspect_untrusted_source(
                    ContentGuardRequest.for_source(
                        task_id=run_input.context.task_id,
                        user_id=run_input.context.user_id,
                        task_type=run_input.context.task_type,
                        tool_name=tool_name,
                        source=source,
                    )
                )
            except Exception:
                guard_decision = ContentGuardDecision(
                    outcome="block",
                    rule_code="content_guard_error",
                    audit_summary="Untrusted source was excluded because content governance failed.",
                )
            await self._record_content_decision(
                scope="untrusted_tool_result",
                decision=guard_decision,
                tool_name=tool_name,
            )
            if guard_decision.blocked or guard_decision.source is None:
                continue
            retained = guard_decision.source
            retained_sources.append(retained.to_workflow_dict())
            history_sources.append(format_untrusted_source_payload(retained))

        return (
            self._safe_json(
                {
                    "query": result.query,
                    "sources": history_sources,
                }
            ),
            retained_sources,
        )

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
