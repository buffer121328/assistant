from __future__ import annotations

import asyncio
import json
from typing import Any, TypedDict, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from packages.model_gateway import sanitize_text
from packages.observability import NoopObservability, Observability
from packages.tools import (
    SearchWebResult,
    ToolCatalogSnapshot,
    ToolInvocation,
    ToolRegistry,
    ToolSnapshotStaleError,
    build_planned_tool_schemas,
)

from .agent_model import (
    AgentDecision,
    AgentModelProtocol,
    build_agent_model_request,
)
from .executors import AgentRunInput, AgentRunResult
from .loop import ControlledLoop


_AGENT_CORE_VERSION = "v1"


class _ExecutionState(TypedDict, total=False):
    tool_schemas: list[dict[str, Any]]
    history: list[dict[str, Any]]
    decision: dict[str, Any]
    display_plan: list[str]
    sources: list[dict[str, Any]]
    tool_calls: list[str]
    requested_tools: list[str]
    result_text: str
    step_count: int


class LangGraphExecutor:
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
    ) -> None:
        self.session = session
        self.tool_registry = tool_registry
        self.model = model
        self.checkpointer = checkpointer
        self.sensitive_values = sensitive_values
        self.tool_snapshot = tool_snapshot
        self.observability = observability or NoopObservability()

    async def execute(self, *, run_input: AgentRunInput) -> AgentRunResult:
        loop = ControlledLoop(
            session=self.session,
            task_id=run_input.context.task_id,
            max_steps=run_input.plan.max_steps,
            sensitive_values=self.sensitive_values,
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
            "step_count": 0,
        }
        previous_snapshot = await self._snapshot(graph, config)
        if previous_snapshot is not None and previous_snapshot.interrupts:
            previous_values = cast(_ExecutionState, previous_snapshot.values)
            loop.steps_executed = int(previous_values.get("step_count", 0))
            graph_input: _ExecutionState | Command = Command(resume=True)
        else:
            graph_input = initial_state
        async with asyncio.timeout(run_input.plan.timeout_seconds):
            final_state = cast(
                _ExecutionState,
                await graph.ainvoke(graph_input, config=config),
            )

        snapshot = await self._snapshot(graph, config)
        checkpoint_id = self._checkpoint_id(snapshot)
        if snapshot is not None and snapshot.interrupts:
            interrupted_state = cast(_ExecutionState, snapshot.values)
            requested_tools = _requested_tools_from_interrupts(snapshot.interrupts)
            return AgentRunResult(
                result_text="任务需要审批后才能继续。",
                display_plan=tuple(interrupted_state.get("display_plan", [])),
                tool_calls=tuple(interrupted_state.get("tool_calls", [])),
                requested_tools=requested_tools,
                loop_steps=loop.steps_executed,
                checkpoint_id=checkpoint_id,
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

    def _build_graph(
        self,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> Any:
        async def prepare(state: _ExecutionState) -> _ExecutionState:
            return await self._prepare(state, run_input, loop)

        async def model(state: _ExecutionState) -> _ExecutionState:
            return await self._model(state, run_input, loop)

        async def tool(state: _ExecutionState) -> _ExecutionState:
            return await self._tool(state, run_input, loop)

        async def approval(state: _ExecutionState) -> _ExecutionState:
            return await self._approval(state, run_input, loop)

        def route_after_model(state: _ExecutionState) -> str:
            return self._route_after_model(state, run_input)

        graph = StateGraph(_ExecutionState)
        graph.add_node("prepare", prepare)
        graph.add_node("model", model)
        graph.add_node("tool", tool)
        graph.add_node("approval", approval)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "model")
        graph.add_conditional_edges(
            "model",
            route_after_model,
            {"tool": "tool", "approval": "approval", "final": END},
        )
        graph.add_edge("approval", "tool")
        graph.add_edge("tool", "model")
        return graph.compile(checkpointer=self.checkpointer)

    async def _prepare(
        self,
        _state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        async def prepare() -> _ExecutionState:
            return {"tool_schemas": list(self.planned_tool_schemas(run_input))}

        update = await self._run_observed_step(
            "prepare",
            run_input,
            lambda: loop.run_step("prepare", prepare),
        )
        update["step_count"] = loop.steps_executed
        return update

    async def _model(
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        async def decide() -> _ExecutionState:
            request = build_agent_model_request(
                run_input,
                tool_schemas=tuple(state.get("tool_schemas", [])),
                history=tuple(state.get("history", [])),
                sensitive_values=self.sensitive_values,
            )
            decision = await self.model.decide(request)
            update: _ExecutionState = {
                "decision": _decision_payload(decision),
                "display_plan": list(decision.plan),
            }
            if decision.action == "final":
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
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
    ) -> str:
        decision = state.get("decision", {})
        if decision.get("action") != "tool_call":
            return "final"
        tool_name = decision.get("tool_name")
        if tool_name in run_input.plan.approval_required_tools:
            return "approval"
        return "tool"

    async def _approval(
        self,
        state: _ExecutionState,
        _run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        if self.checkpointer is None:
            raise RuntimeError("Approval interrupt requires a checkpointer")

        async def wait_for_approval() -> _ExecutionState:
            decision = state.get("decision", {})
            tool_name = decision.get("tool_name")
            if not isinstance(tool_name, str):
                raise RuntimeError("Approval tool decision is unavailable")
            interrupt(
                {
                    "type": "tool_approval",
                    "tool_name": tool_name,
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

    async def _tool(
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        async def call_tool() -> _ExecutionState:
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
                tool_snapshot_revision=(
                    run_input.plan.tool_snapshot_revision or None
                ),
                tool_version=dict(run_input.plan.tool_versions).get(tool_name),
            )
            with self.observability.observe(
                "agent.tool.call",
                as_type="tool",
                input={"tool_name": tool_name, "arguments": arguments},
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

    async def _run_observed_step(
        self,
        step_name: str,
        run_input: AgentRunInput,
        operation: Any,
    ) -> _ExecutionState:
        with self.observability.observe(
            f"agent.graph.{step_name}",
            input={"step": step_name},
            metadata={
                "task_id": run_input.context.task_id,
                "agent_core_version": _AGENT_CORE_VERSION,
            },
        ) as observation:
            result = cast(_ExecutionState, await operation())
            observation.update(output={"status": "success"})
            return result

    def planned_tool_schemas(
        self,
        run_input: AgentRunInput,
    ) -> tuple[dict[str, Any], ...]:
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

    async def _snapshot(self, graph: Any, config: dict[str, Any]) -> Any | None:
        if self.checkpointer is None:
            return None
        return await graph.aget_state(config)

    def _checkpoint_id(self, snapshot: Any | None) -> str | None:
        if snapshot is None:
            return None
        configurable = snapshot.config.get("configurable", {})
        checkpoint_id = configurable.get("checkpoint_id")
        return str(checkpoint_id) if checkpoint_id else None

    def _safe_json(self, value: Any) -> str:
        return sanitize_text(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ),
            extra_sensitive_values=self.sensitive_values,
        )


def _decision_payload(decision: AgentDecision) -> dict[str, Any]:
    return {
        "action": decision.action,
        "answer": decision.answer,
        "tool_name": decision.tool_name,
        "arguments": decision.arguments,
        "plan": list(decision.plan),
    }


def _requested_tools_from_interrupts(interrupts: tuple[Any, ...]) -> tuple[str, ...]:
    names: list[str] = []
    for item in interrupts:
        value = getattr(item, "value", None)
        if not isinstance(value, dict):
            continue
        tool_name = value.get("tool_name")
        if isinstance(tool_name, str) and tool_name not in names:
            names.append(tool_name)
    return tuple(names)
