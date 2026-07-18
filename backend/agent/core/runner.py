from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent.memory import SemanticMemory, load_memory_context
from model_gateway import sanitize_text
from agent.tool_management import ToolCandidateSelector, ToolCatalogSnapshot

from agent.planning.capabilities import (
    CapabilitiesBuilder,
    ToolCapability,
    snapshot_from_tool_selection,
)
from agent.planning.context import ContextBuilder, TaskContext
from agent.modeling.executors import AgentExecutorProtocol, AgentRunInput, AgentRunResult
from agent.planning.planner import DefaultPlanningLayer, ExecutionPlan, PlanningLayerProtocol
from agent.ports import (
    ConversationContextPack,
    ConversationContextPort,
    ExecutionTracePort,
    LocalTaskServicePort,
    TaskLifecyclePort,
    UserLookupPort,
)
from agent.planning.profiles import DefaultProfileSelector
from agent.skill_management import SkillsLoader


LANGGRAPH_EXECUTOR_TOOL_NAME = "langgraph.executor"
TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_WAITING_APPROVAL = "waiting_approval"


class AgentHarnessError(Exception):
    pass


class NonPendingTaskExecutionError(AgentHarnessError):
    pass


LangGraphExecutionResult = AgentRunResult


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str
    result_text: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    workflow_key: str | None = None


class MinimalLangGraphExecutor:
    async def execute(self, *, run_input: AgentRunInput) -> AgentRunResult:
        plan = run_input.plan
        context = run_input.context
        tool_calls = ("search.web",) if context.sources else ()
        return AgentRunResult(
            result_text=_build_langgraph_result(plan, context),
            tool_calls=tool_calls,
            loop_steps=min(len(plan.steps), plan.max_steps),
            checkpoint_id=f"ckpt-{context.task_id[:8]}",
        )


class ExecutionBoundary:
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
        policy_outcome = _tool_policy_outcome(
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
                    "result_text": _truncate(result.result_text),
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

        from agent.core.compat import record_execution_trace

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


class AgentHarness:
    def __init__(
        self,
        *,
        session: AsyncSession,
        executor: Any,
        search_tool: Any | None = None,
        planning_layer: PlanningLayerProtocol | None = None,
        profile_selector: DefaultProfileSelector | None = None,
        skills_loader: SkillsLoader | None = None,
        capabilities_builder: CapabilitiesBuilder | None = None,
        context_builder: ContextBuilder | None = None,
        tool_snapshot: ToolCatalogSnapshot | None = None,
        tool_candidate_selector: ToolCandidateSelector | None = None,
        core_tools: tuple[str, ...] = (),
        tool_count_budget: int = 15,
        semantic_memory: SemanticMemory | None = None,
        semantic_memory_limit: int = 5,
        event_sink: Callable[[str, dict[str, object]], Awaitable[None]] | None = None,
        memory_candidate_hook: Callable[[Any], Awaitable[None]] | None = None,
        task_lifecycle: TaskLifecyclePort[Any] | None = None,
        local_tasks: LocalTaskServicePort[Any] | None = None,
        conversation_context: ConversationContextPort | None = None,
        user_lookup: UserLookupPort[Any] | None = None,
    ) -> None:
        self.session = session
        self.executor = executor
        self.search_tool = search_tool
        self.planning_layer = planning_layer or DefaultPlanningLayer()
        self.profile_selector = profile_selector or DefaultProfileSelector()
        self.skills_loader = skills_loader or SkillsLoader(_default_skills_root())
        self.capabilities_builder = capabilities_builder or CapabilitiesBuilder(
            (
                ToolCapability(
                    name="search.web",
                    description="Search public web sources",
                    enabled=search_tool is not None,
                ),
                ToolCapability(
                    name="shell.exec",
                    description="Execute a local shell command",
                    enabled=False,
                    approval_required=True,
                ),
            )
        )
        self.context_builder = context_builder or ContextBuilder()
        self.tool_snapshot = tool_snapshot
        self.tool_candidate_selector = (
            tool_candidate_selector or ToolCandidateSelector()
        )
        self.core_tools = core_tools
        self.tool_count_budget = max(0, min(tool_count_budget, 15))
        self.semantic_memory = semantic_memory
        self.semantic_memory_limit = max(1, min(semantic_memory_limit, 20))
        self.event_sink = event_sink
        self.memory_candidate_hook = memory_candidate_hook
        self.task_lifecycle = task_lifecycle
        self.local_tasks = local_tasks
        self.conversation_context = conversation_context
        self.user_lookup = user_lookup

    async def execute_task(self, task_id: str) -> Any:
        task = await self._load_pending_task(task_id)
        if task.task_type == "memory":
            if self.local_tasks is not None:
                return await self.local_tasks.execute_memory_task(task.id)
            from agent.core.compat import execute_memory_task

            return await execute_memory_task(
                self.session,
                task_id=task.id,
                semantic_memory=self.semantic_memory,
            )
        if task.task_type == "status":
            if self.local_tasks is not None:
                return await self.local_tasks.execute_status_task(task.id)
            from agent.core.compat import execute_status_task

            return await execute_status_task(self.session, task_id=task.id)

        user = await self._load_user(task.user_id)
        profile = self.profile_selector.select(task)
        memory_retrieval = await self._memory_context(
            user.id,
            query=str(task.input_text),
            task_id=task.id,
            conversation_id=task.conversation_id,
        )
        memory_summary = "\n".join(item.content for item in memory_retrieval.items)
        skills = self.skills_loader.load(profile.skill_names)
        if self.tool_snapshot is None:
            capabilities = self.capabilities_builder.build(
                requested_tools=profile.requested_tools
            )
        else:
            selection = self.tool_candidate_selector.select(
                self.tool_snapshot,
                task_type=str(task.task_type),
                profile_name=profile.name,
                skill_names=tuple(skill.name for skill in skills),
                requested_tools=profile.requested_tools,
                core_tools=self.core_tools,
                budget=self.tool_count_budget,
            )
            capabilities = snapshot_from_tool_selection(
                self.tool_snapshot,
                selection,
            )
        conversation_history: tuple[tuple[str, str], ...] = ()
        conversation_summary = ""
        memory_blocks: tuple[str, ...] = ()
        context_trace: tuple[dict[str, Any], ...] = (
            {
                "section": "retrieved_memory",
                "trace_id": memory_retrieval.trace_id,
                "mode": memory_retrieval.mode,
                "time_intent": memory_retrieval.time_intent,
                "memory_ids": tuple(item.memory_id for item in memory_retrieval.items),
                "injected_tokens": memory_retrieval.injected_tokens,
            },
        )
        conversation_compacted = False
        if task.conversation_id is not None:
            pack = await self._conversation_context_pack(
                conversation_id=task.conversation_id,
                user_id=task.user_id,
                task_id=task.id,
                current_input=str(task.input_text),
                long_term_memory=memory_summary,
            )
            conversation_history = pack.history
            conversation_summary = pack.summary
            memory_blocks = pack.memory_blocks
            context_trace = context_trace + pack.trace
            conversation_compacted = pack.compacted

        context = self.context_builder.build(
            task=task,
            user=user,
            memory_summary=memory_summary,
            skills=skills,
            capabilities=capabilities,
            conversation_history=conversation_history,
            conversation_summary=conversation_summary,
            memory_blocks=memory_blocks,
            context_trace=context_trace,
            conversation_compacted=conversation_compacted,
        )
        plan = self.planning_layer.build_plan(
            task=task,
            profile=profile,
            context=context,
        )

        if self.event_sink is not None:
            await self.event_sink("plan", {"steps": list(plan.steps)})

        if self.task_lifecycle is None:
            task.status = TASK_STATUS_RUNNING
            task.workflow_key = profile.workflow_key
            task.error_message = None
            await self.session.commit()
            await self.session.refresh(task)
        else:
            task = await self.task_lifecycle.mark_running(
                task.id, workflow_key=profile.workflow_key
            )

        outcome = await self._execute_boundary(
            task=task,
            user=user,
            context=context,
            plan=plan,
        )
        return await self._persist_outcome(
            task_id=task.id,
            outcome=outcome,
        )

    async def _execute_boundary(
        self,
        *,
        task: Any,
        user: Any,
        context: TaskContext,
        plan: ExecutionPlan,
    ) -> ExecutionOutcome:
        return await self.executor.execute(
            task=task,
            user=user,
            plan=plan,
            context=context,
        )

    async def _persist_outcome(
        self,
        *,
        task_id: str,
        outcome: ExecutionOutcome,
    ) -> Any:
        task_lifecycle: Any
        if self.task_lifecycle is None:
            from agent.core.compat import task_lifecycle as build_task_lifecycle

            task_lifecycle = build_task_lifecycle(
                self.session, success_hook=self.memory_candidate_hook
            )
        else:
            task_lifecycle = self.task_lifecycle
        if outcome.status == TASK_STATUS_WAITING_APPROVAL:
            message = (
                outcome.result_text
                or outcome.error_message
                or "任务需要审批后才能继续。"
            )
            requested_tools = outcome.metadata.get("requested_tools", [])
            approval_requests = outcome.metadata.get("approval_requests", [])
            return await task_lifecycle.save_waiting_approval(
                task_id,
                message,
                requested_tools=(
                    tool for tool in requested_tools if isinstance(tool, str)
                ),
                approval_requests=(
                    request
                    for request in approval_requests
                    if isinstance(request, dict)
                ),
            )
        if outcome.status == TASK_STATUS_FAILED:
            return await task_lifecycle.save_failure(
                task_id,
                outcome.error_message or "任务执行失败。",
            )
        result_text = outcome.result_text or "任务已完成。"
        return await task_lifecycle.save_success(task_id, result_text)

    async def _load_pending_task(self, task_id: str) -> Any:
        if self.task_lifecycle is not None:
            return await self.task_lifecycle.load_pending(task_id)
        from agent.core.compat import load_pending_task

        return await load_pending_task(
            self.session,
            task_id=task_id,
            pending_status=TASK_STATUS_PENDING,
            not_pending_error=NonPendingTaskExecutionError,
            not_found_error=AgentHarnessError,
        )

    async def _load_user(self, user_id: str) -> Any:
        if self.user_lookup is not None:
            return await self.user_lookup.load_user(user_id)
        from agent.core.compat import load_user

        return await load_user(
            self.session,
            user_id=user_id,
            not_found_error=AgentHarnessError,
        )

    async def _conversation_context_pack(
        self,
        *,
        conversation_id: str,
        user_id: str,
        task_id: str,
        current_input: str,
        long_term_memory: str,
    ) -> ConversationContextPack:
        if self.conversation_context is not None:
            return await self.conversation_context.load_context(
                conversation_id=conversation_id,
                user_id=user_id,
                task_id=task_id,
                current_input=current_input,
                long_term_memory=long_term_memory,
            )

        from agent.core.compat import load_conversation_context

        return await load_conversation_context(
            self.session,
            conversation_id=conversation_id,
            user_id=user_id,
            task_id=task_id,
            current_input=current_input,
            long_term_memory=long_term_memory,
        )

    async def _memory_context(
        self,
        user_id: str,
        *,
        query: str,
        task_id: str,
        conversation_id: str | None,
    ):
        return await load_memory_context(
            session=self.session,
            user_id=user_id,
            query=query,
            semantic_memory=self.semantic_memory,
            semantic_limit=self.semantic_memory_limit,
            task_id=task_id,
            conversation_id=conversation_id,
        )


def _default_skills_root() -> Path:
    return Path(__file__).resolve().parents[2] / "resources" / "skillpacks"


def _build_langgraph_result(plan: ExecutionPlan, context: TaskContext) -> str:
    lines = [f"目标: {plan.goal}", "", "执行步骤:"]
    for index, step in enumerate(plan.steps, start=1):
        lines.append(f"{index}. {step}")

    if context.memory_summary:
        lines.extend(["", f"记忆摘要: {context.memory_summary}"])

    if context.sources:
        lines.extend(["", "参考来源:"])
        for source in context.sources:
            title = source.get("title") or source.get("url") or "来源"
            url = source.get("url")
            lines.append(f"- {title}" + (f" - {url}" if url else ""))

    return "\n".join(lines)


def _tool_policy_outcome(
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


def _truncate(value: str, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."
