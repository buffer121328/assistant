from __future__ import annotations

import asyncio
import json
from typing import Any, TypedDict, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import Approval, ApprovalStatus, Task
from agent.tool_management.approval import (
    EXACT_APPROVAL_TOOLS,
    external_approval_binding,
    external_audit_arguments,
)
from model_gateway import sanitize_text
from observability import NoopObservability, Observability
from agent.tool_management import (
    SearchWebResult,
    ToolCatalogSnapshot,
    ToolInvocation,
    ToolRegistry,
    ToolSnapshotStaleError,
    build_planned_tool_schemas,
)

from agent.modeling.agent_model import (
    AgentDecision,
    AgentModelProtocol,
    WorkPlan,
    WorkPlanStep,
    build_agent_model_request,
    build_review_model_request,
    build_work_plan_request,
)
from agent.modeling.executors import (
    AgentRunInput,
    AgentRunResult,
    ApprovalTypeName,
    HumanApprovalRequest,
)
from agent.core.loop import ControlledLoop
from agent.core.subagents import SubAgentCoordinator, SubAgentRequest


_AGENT_CORE_VERSION = "v2"


class _ExecutionState(TypedDict, total=False):
    tool_schemas: list[dict[str, Any]]
    history: list[dict[str, Any]]
    decision: dict[str, Any]
    display_plan: list[str]
    sources: list[dict[str, Any]]
    tool_calls: list[str]
    requested_tools: list[str]
    result_text: str
    candidate_result: str
    work_plan: dict[str, Any]
    review_decision: dict[str, str]
    review_feedback: str
    review_retry_count: int
    replan_count: int
    step_count: int
    subagent_results: list[dict[str, Any]]


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
        subagent_coordinator: SubAgentCoordinator | None = None,
        prompt_builder: Any | None = None,
    ) -> None:
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
        async with asyncio.timeout(run_input.plan.timeout_seconds):
            final_state = cast(
                _ExecutionState,
                await graph.ainvoke(graph_input, config=config),
            )

        snapshot = await self._snapshot(graph, config)
        checkpoint_id = self._checkpoint_id(snapshot)
        if snapshot is not None and snapshot.interrupts:
            interrupted_state = cast(_ExecutionState, snapshot.values)
            approval_requests = _approval_requests_from_interrupts(
                snapshot.interrupts
            )
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

    def _build_graph(
        self,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> Any:
        async def prepare(state: _ExecutionState) -> _ExecutionState:
            return await self._prepare(state, run_input, loop)

        async def model(state: _ExecutionState) -> _ExecutionState:
            return await self._model(state, run_input, loop)

        async def plan(state: _ExecutionState) -> _ExecutionState:
            return await self._plan(state, run_input, loop)

        async def review(state: _ExecutionState) -> _ExecutionState:
            return await self._review(state, run_input, loop)

        async def finalize(state: _ExecutionState) -> _ExecutionState:
            return await self._finalize(state, run_input, loop)

        async def fail(state: _ExecutionState) -> _ExecutionState:
            return await self._fail_review(state, run_input, loop)

        async def tool(state: _ExecutionState) -> _ExecutionState:
            return await self._tool(state, run_input, loop)

        async def tool_batch(state: _ExecutionState) -> _ExecutionState:
            return await self._tool_batch(state, run_input, loop)

        async def subagents(state: _ExecutionState) -> _ExecutionState:
            return await self._subagents(state, run_input, loop)

        async def approval(state: _ExecutionState) -> _ExecutionState:
            return await self._approval(state, run_input, loop)

        async def plan_approval(state: _ExecutionState) -> _ExecutionState:
            return await self._plan_approval(state, run_input, loop)

        async def human_review(state: _ExecutionState) -> _ExecutionState:
            return await self._human_review(state, run_input, loop)

        def route_after_model(state: _ExecutionState) -> str:
            return self._route_after_model(state, run_input)

        def route_after_prepare(_state: _ExecutionState) -> str:
            if run_input.plan.execution_mode == "plan_execute_review":
                return "plan"
            return "model"

        def route_after_plan(state: _ExecutionState) -> str:
            if run_input.plan.require_plan_approval:
                return "approval"
            return "subagents" if self._should_delegate(state, run_input) else "model"

        def route_after_plan_approval(state: _ExecutionState) -> str:
            return "subagents" if self._should_delegate(state, run_input) else "model"

        def route_after_review(state: _ExecutionState) -> str:
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
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
    ) -> str:
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
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
    ) -> bool:
        if self.subagent_coordinator is None or run_input.plan.max_subagents <= 0:
            return False
        work_plan = _work_plan_from_state(state)
        return bool(
            work_plan
            and any(step.agent_role is not None for step in work_plan.steps)
        )

    async def _subagents(
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        async def delegate() -> _ExecutionState:
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

    async def _plan(
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        async def create_plan() -> _ExecutionState:
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

    async def _plan_approval(
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        if self.checkpointer is None:
            raise RuntimeError("Plan approval interrupt requires a checkpointer")

        async def wait_for_approval() -> _ExecutionState:
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

    async def _review(
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        async def review_candidate() -> _ExecutionState:
            work_plan = _work_plan_from_state(state)
            candidate_result = state.get("candidate_result", "")
            if work_plan is None or not candidate_result:
                raise RuntimeError("Review state is unavailable")
            request = build_review_model_request(
                run_input,
                work_plan=work_plan,
                candidate_result=candidate_result,
                sensitive_values=self.sensitive_values,
            )
            decision = await self.model.review(request)
            update: _ExecutionState = {
                "review_decision": {
                    "status": decision.status,
                    "feedback": decision.feedback,
                },
                "review_feedback": decision.feedback,
            }
            if decision.status == "retry":
                retry_count = int(state.get("review_retry_count", 0))
                if retry_count >= run_input.plan.max_review_retries:
                    raise RuntimeError("Review retry budget exhausted")
                history = list(state.get("history", []))
                history.append(
                    {
                        "role": "review",
                        "name": "review.feedback",
                        "content": decision.feedback,
                    }
                )
                update.update(
                    {
                        "review_retry_count": retry_count + 1,
                        "history": history,
                        "candidate_result": "",
                    }
                )
            elif decision.status == "replan":
                replan_count = int(state.get("replan_count", 0))
                if replan_count >= run_input.plan.max_replans:
                    raise RuntimeError("Review replan budget exhausted")
                update.update(
                    {
                        "replan_count": replan_count + 1,
                        "candidate_result": "",
                    }
                )
            return update

        update = await self._run_observed_step(
            "review",
            run_input,
            lambda: loop.run_step("review", review_candidate),
        )
        update["step_count"] = loop.steps_executed
        return update

    def _route_after_review(self, state: _ExecutionState) -> str:
        status = state.get("review_decision", {}).get("status")
        if status == "pass":
            return "finalize"
        if status == "retry":
            return "retry"
        if status == "replan":
            return "replan"
        if status == "escalate":
            return "human_review"
        return "fail"

    async def _human_review(
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        if self.checkpointer is None:
            raise RuntimeError("Human review interrupt requires a checkpointer")

        async def wait_for_review() -> _ExecutionState:
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

    async def _finalize(
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        async def finalize() -> _ExecutionState:
            candidate = state.get("candidate_result", "")
            if not candidate:
                raise RuntimeError("Reviewed candidate is unavailable")
            return {"result_text": candidate}

        update = await self._run_observed_step(
            "finalize",
            run_input,
            lambda: loop.run_step("finalize", finalize),
        )
        update["step_count"] = loop.steps_executed
        return update

    async def _fail_review(
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        async def fail() -> _ExecutionState:
            feedback = state.get("review_feedback", "Review rejected candidate")
            raise RuntimeError(f"Review failed: {feedback}")

        return await self._run_observed_step(
            "review_failure",
            run_input,
            lambda: loop.run_step("review_failure", fail),
        )

    async def _is_human_approved(
        self,
        run_input: AgentRunInput,
        *,
        approval_type: str,
        subject: str,
    ) -> bool:
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
        self,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        async def call_tools() -> _ExecutionState:
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
        "tool_calls": [
            {
                "id": call.call_id,
                "tool_name": call.tool_name,
                "arguments": call.arguments,
            }
            for call in decision.tool_calls
        ],
    }


def _approval_requests_from_interrupts(
    interrupts: tuple[Any, ...],
) -> tuple[HumanApprovalRequest, ...]:
    requests: list[HumanApprovalRequest] = []
    for item in interrupts:
        value = getattr(item, "value", None)
        if not isinstance(value, dict):
            continue
        approval_type = value.get("approval_type")
        if approval_type not in {"tool", "plan", "review"}:
            approval_type = "tool" if value.get("tool_name") else None
        subject = value.get("subject") or value.get("tool_name")
        summary = value.get("summary") or "需要人工审批。"
        if (
            approval_type in {"tool", "plan", "review"}
            and isinstance(subject, str)
            and isinstance(summary, str)
        ):
            request = HumanApprovalRequest(
                approval_type=cast(ApprovalTypeName, approval_type),
                subject=subject[:128],
                summary=summary[:1000],
                tool_name=(
                    value.get("tool_name")
                    if isinstance(value.get("tool_name"), str)
                    else None
                ),
            )
            if request not in requests:
                requests.append(request)
    return tuple(requests)


def _work_plan_payload(work_plan: WorkPlan) -> dict[str, Any]:
    return {
        "goal": work_plan.goal,
        "steps": [
            {
                "objective": step.objective,
                "acceptance_criteria": list(step.acceptance_criteria),
                "agent_role": step.agent_role,
            }
            for step in work_plan.steps
        ],
    }


def _work_plan_from_state(state: _ExecutionState) -> WorkPlan | None:
    payload = state.get("work_plan")
    if not isinstance(payload, dict):
        return None
    goal = payload.get("goal")
    raw_steps = payload.get("steps")
    if not isinstance(goal, str) or not isinstance(raw_steps, list):
        return None
    steps: list[WorkPlanStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            return None
        objective = item.get("objective")
        criteria = item.get("acceptance_criteria")
        agent_role = item.get("agent_role")
        if not isinstance(objective, str) or not isinstance(criteria, list):
            return None
        if not all(isinstance(value, str) for value in criteria):
            return None
        if agent_role is not None and not isinstance(agent_role, str):
            return None
        steps.append(
            WorkPlanStep(
                objective=objective,
                acceptance_criteria=tuple(criteria),
                agent_role=agent_role,
            )
        )
    return WorkPlan(goal=goal, steps=tuple(steps))


def _work_plan_summary(state: _ExecutionState) -> str:
    work_plan = _work_plan_from_state(state)
    if work_plan is None:
        raise RuntimeError("Work plan is unavailable for approval")
    return "；".join(step.objective for step in work_plan.steps)[:1000]
