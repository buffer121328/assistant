from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import Task, TaskStatus, ToolLog, User
from assistant_api.services import MemoryService, StatusService, TaskService
from packages.memory import SemanticMemory, load_memory_context
from packages.model_gateway import sanitize_text
from packages.tools import ToolCandidateSelector, ToolCatalogSnapshot

from .capabilities import (
    CapabilitiesBuilder,
    ToolCapability,
    snapshot_from_tool_selection,
)
from .context import ContextBuilder, TaskContext
from .executors import AgentExecutorProtocol, AgentRunInput, AgentRunResult
from .planner import DefaultPlanningLayer, ExecutionPlan, PlanningLayerProtocol
from .profiles import DefaultProfileSelector
from .skills import SkillsLoader


LANGGRAPH_EXECUTOR_TOOL_NAME = "langgraph.executor"


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
    ) -> None:
        self.session = session
        self.langgraph_executor = langgraph_executor
        self.sensitive_values = tuple(sensitive_values)

    async def execute(
        self,
        *,
        task: Task,
        user: User,
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
                status=TaskStatus.FAILED.value,
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
                    if policy_outcome.status == TaskStatus.WAITING_APPROVAL.value
                    else None
                ),
                error_message=(
                    None
                    if policy_outcome.status == TaskStatus.WAITING_APPROVAL.value
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
            status=TaskStatus.SUCCESS.value,
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
        self.session.add(
            ToolLog(
                task_id=task_id,
                tool_name=LANGGRAPH_EXECUTOR_TOOL_NAME,
                status=status,
                input_text=input_text,
                output_text=output_text,
                error_message=error_message,
            )
        )
        await self.session.flush()

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
        memory_candidate_hook: Callable[[Task], Awaitable[None]] | None = None,
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

    async def execute_task(self, task_id: str) -> Task:
        task = await self._load_pending_task(task_id)
        if task.task_type == "memory":
            return await MemoryService(
                self.session,
                semantic_memory=self.semantic_memory,
            ).execute_task(task.id)
        if task.task_type == "status":
            return await StatusService(self.session).execute_task(task.id)

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
            from assistant_api.conversation_memory import ConversationMemoryService
            from assistant_api.conversations import ConversationService
            from packages.memory.working_set import (
                ConversationMessageRef,
                build_context_pack,
            )

            messages = await ConversationService(self.session).list_messages(
                conversation_id=task.conversation_id,
                user_id=task.user_id,
                limit=200,
                exclude_task_id=task.id,
            )
            conversation_memory = ConversationMemoryService(self.session)
            summary = await conversation_memory.get_active_summary(
                conversation_id=task.conversation_id,
                user_id=task.user_id,
            )
            blocks = await conversation_memory.list_blocks(
                user_id=task.user_id,
                conversation_id=task.conversation_id,
            )
            pack = build_context_pack(
                memory_blocks=tuple((block.id, block.content) for block in blocks),
                conversation_summary=summary.summary_text if summary else "",
                summary_source_ids=(
                    (summary.source_start_message_id, summary.source_end_message_id)
                    if summary
                    else ()
                ),
                summary_version=summary.summary_version if summary else None,
                long_term_memory=memory_summary,
                messages=tuple(
                    ConversationMessageRef(message.id, message.role, message.content)
                    for message in messages
                ),
                current_input=str(task.input_text),
            )
            conversation_history = tuple(
                (message.role, message.content) for message in pack.recent_turns
            )
            conversation_summary = pack.conversation_summary
            memory_blocks = pack.memory_blocks
            context_trace = context_trace + tuple(
                {
                    "section": item.section,
                    "estimated_tokens": item.estimated_tokens,
                    "source_ids": item.source_ids,
                    "truncated_source_ids": item.truncated_source_ids,
                    "version": item.version,
                }
                for item in pack.trace
            )
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

        task.status = TaskStatus.RUNNING.value
        task.workflow_key = profile.workflow_key
        task.error_message = None
        await self.session.commit()
        await self.session.refresh(task)

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
        task: Task,
        user: User,
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
    ) -> Task:
        task_service = TaskService(
            self.session, success_hook=self.memory_candidate_hook
        )
        if outcome.status == TaskStatus.WAITING_APPROVAL.value:
            message = (
                outcome.result_text
                or outcome.error_message
                or "任务需要审批后才能继续。"
            )
            requested_tools = outcome.metadata.get("requested_tools", [])
            approval_requests = outcome.metadata.get("approval_requests", [])
            return await task_service.save_waiting_approval(
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
        if outcome.status == TaskStatus.FAILED.value:
            return await task_service.save_failure(
                task_id,
                outcome.error_message or "任务执行失败。",
            )
        result_text = outcome.result_text or "任务已完成。"
        return await task_service.save_success(task_id, result_text)

    async def _load_pending_task(self, task_id: str) -> Task:
        task = await self.session.get(Task, task_id)
        if task is None:
            raise AgentHarnessError(f"Task not found: {task_id}")
        if task.status != TaskStatus.PENDING.value:
            raise NonPendingTaskExecutionError(
                f"Task is not pending: {task.id} ({task.status})"
            )
        return task

    async def _load_user(self, user_id: str) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise AgentHarnessError(f"User not found: {user_id}")
        return user

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
    return Path(__file__).resolve().parents[2] / "prompts" / "skills"


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
            status=TaskStatus.FAILED.value,
            error_message=f"执行计划未授权工具：{joined}。",
            metadata={"requested_tools": list(requested)},
            workflow_key=plan.workflow_key,
        )

    if approval_requests:
        return ExecutionOutcome(
            status=TaskStatus.WAITING_APPROVAL.value,
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
            status=TaskStatus.WAITING_APPROVAL.value,
            result_text=f"任务需要审批后才能继续：{joined}。",
            metadata={"requested_tools": list(requested)},
            workflow_key=plan.workflow_key,
        )

    return None


def _truncate(value: str, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."
