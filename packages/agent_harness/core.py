from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import Task, TaskStatus, ToolLog, User
from assistant_api.services import MemoryService, StatusService, TaskService
from packages.memory import load_memory_summary
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
                "approval_requests": [
                    asdict(request) for request in approval_requests
                ],
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
            "approval_requests": [
                asdict(request) for request in approval_requests
            ],
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

    async def execute_task(self, task_id: str) -> Task:
        task = await self._load_pending_task(task_id)
        if task.task_type == "memory":
            return await MemoryService(self.session).execute_task(task.id)
        if task.task_type == "status":
            return await StatusService(self.session).execute_task(task.id)

        user = await self._load_user(task.user_id)
        profile = self.profile_selector.select(task)
        memory_summary = await self._memory_summary(user.id)
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
        context = self.context_builder.build(
            task=task,
            user=user,
            memory_summary=memory_summary,
            skills=skills,
            capabilities=capabilities,
        )
        plan = self.planning_layer.build_plan(
            task=task,
            profile=profile,
            context=context,
        )

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
        task_service = TaskService(self.session)
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

    async def _memory_summary(self, user_id: str) -> str:
        return await load_memory_summary(session=self.session, user_id=user_id)


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
