from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent.planning.capabilities import (
    CapabilitiesBuilder,
    ToolCapability,
    snapshot_from_tool_selection,
)
from agent.planning.context import ContextBuilder, TaskContext
from agent.planning.planner import (
    DefaultPlanningLayer,
    ExecutionPlan,
    PlanningLayerProtocol,
)
from agent.planning.profiles import DefaultProfileSelector
from agent.ports import (
    ConversationContextPack,
    ConversationContextPort,
    LocalTaskServicePort,
    TaskLifecyclePort,
    UserLookupPort,
)
from agent.skill_management import SkillsLoader
from memory import SemanticMemory, load_memory_context
from runtime.runner_events import safe_event_payload, truncate
from runtime.runner_types import (
    AgentHarnessError,
    LANGGRAPH_EXECUTOR_TOOL_NAME,
    NonPendingTaskExecutionError,
    TASK_STATUS_FAILED,
    TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCESS,
    TASK_STATUS_WAITING_APPROVAL,
    ExecutionOutcome,
)
from tools.core.catalog import ToolCandidateSelector, ToolCatalogSnapshot


class AgentHarness:
    """表示 处理 agent harness 的后端数据结构或服务对象。"""

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
        """初始化对象实例。

        Args:
            session: session 参数。
            executor: executor 参数。
            search_tool: search_tool 参数。
            planning_layer: planning_layer 参数。
            profile_selector: profile_selector 参数。
            skills_loader: skills_loader 参数。
            capabilities_builder: capabilities_builder 参数。
            context_builder: context_builder 参数。
            tool_snapshot: tool_snapshot 参数。
            tool_candidate_selector: tool_candidate_selector 参数。
            core_tools: core_tools 参数。
            tool_count_budget: tool_count_budget 参数。
            semantic_memory: semantic_memory 参数。
            semantic_memory_limit: semantic_memory_limit 参数。
            event_sink: event_sink 参数。
            memory_candidate_hook: memory_candidate_hook 参数。
            task_lifecycle: task_lifecycle 参数。
            local_tasks: local_tasks 参数。
            conversation_context: conversation_context 参数。
            user_lookup: user_lookup 参数。
        """
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
        """执行 task。

        Args:
            task_id: task_id 参数。
        """
        task = await self._load_pending_task(task_id)
        if task.task_type == "memory":
            if self.local_tasks is not None:
                return await self.local_tasks.execute_memory_task(task.id)
            from runtime.compat import execute_memory_task

            return await execute_memory_task(
                self.session,
                task_id=task.id,
                semantic_memory=self.semantic_memory,
            )
        if task.task_type == "status":
            if self.local_tasks is not None:
                return await self.local_tasks.execute_status_task(task.id)
            from runtime.compat import execute_status_task

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

        await self._publish_event(
            "task.started",
            {
                "status": TASK_STATUS_RUNNING,
                "task_type": str(task.task_type),
                "workflow_key": profile.workflow_key,
                "profile_name": profile.name,
            },
        )
        await self._publish_event(
            "task.plan.created",
            {
                "workflow_key": plan.workflow_key,
                "profile_name": plan.profile_name,
                "executor_kind": plan.executor_kind,
                "risk_level": plan.risk_level,
                "steps": list(plan.steps),
                "allowed_tools": list(plan.allowed_tools),
                "approval_required_tools": list(plan.approval_required_tools),
            },
        )
        await self._publish_event("plan", {"steps": list(plan.steps)})

        action_payload = self._action_payload(task=task, plan=plan)
        await self._publish_event("task.action.started", action_payload)
        outcome = await self._execute_boundary(
            task=task,
            user=user,
            context=context,
            plan=plan,
        )
        if outcome.status == TASK_STATUS_FAILED:
            await self._publish_event(
                "task.action.failed",
                {
                    **action_payload,
                    "status": outcome.status,
                    "error_message": outcome.error_message or "任务执行失败。",
                },
            )
        else:
            await self._publish_event(
                "task.action.completed",
                {
                    **action_payload,
                    "status": outcome.status,
                    "requested_tools": list(
                        outcome.metadata.get("requested_tools", [])
                    ),
                    "tool_calls": list(outcome.metadata.get("tool_calls", [])),
                    "loop_steps": outcome.metadata.get("loop_steps"),
                    "checkpoint_id": outcome.metadata.get("checkpoint_id"),
                },
            )
        return await self._persist_outcome(
            task_id=task.id,
            outcome=outcome,
        )

    async def _publish_event(self, event_type: str, payload: dict[str, object]) -> None:
        """执行 发布 event 的内部辅助逻辑。

        Args:
            event_type: event_type 参数。
            payload: payload 参数。
        """
        if self.event_sink is None:
            return
        try:
            await self.event_sink(event_type, safe_event_payload(payload))
        except Exception:
            return

    def _action_payload(self, *, task: Any, plan: ExecutionPlan) -> dict[str, object]:
        """执行 处理 action payload 的内部辅助逻辑。

        Args:
            task: task 参数。
            plan: plan 参数。
        """
        return {
            "action_name": LANGGRAPH_EXECUTOR_TOOL_NAME,
            "task_type": str(task.task_type),
            "workflow_key": plan.workflow_key,
            "profile_name": plan.profile_name,
            "executor_kind": plan.executor_kind,
        }

    async def _execute_boundary(
        self,
        *,
        task: Any,
        user: Any,
        context: TaskContext,
        plan: ExecutionPlan,
    ) -> ExecutionOutcome:
        """执行 执行 boundary 的内部辅助逻辑。

        Args:
            task: task 参数。
            user: user 参数。
            context: context 参数。
            plan: plan 参数。
        """
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
        """执行 处理 persist outcome 的内部辅助逻辑。

        Args:
            task_id: task_id 参数。
            outcome: outcome 参数。
        """
        task_lifecycle: Any
        if self.task_lifecycle is None:
            from runtime.compat import task_lifecycle as build_task_lifecycle

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
            requested_tools = [
                tool
                for tool in outcome.metadata.get("requested_tools", [])
                if isinstance(tool, str)
            ]
            approval_requests = [
                request
                for request in outcome.metadata.get("approval_requests", [])
                if isinstance(request, dict)
            ]
            stored = await task_lifecycle.save_waiting_approval(
                task_id,
                message,
                requested_tools=requested_tools,
                approval_requests=approval_requests,
            )
            await self._publish_event(
                "task.waiting_approval",
                {
                    "status": TASK_STATUS_WAITING_APPROVAL,
                    "requested_tools": requested_tools,
                    "approval_request_count": len(approval_requests),
                    "approval_count": len(set(requested_tools))
                    + len(approval_requests),
                    "summary": message,
                    "message": message,
                },
            )
            return stored
        if outcome.status == TASK_STATUS_FAILED:
            stored = await task_lifecycle.save_failure(
                task_id,
                outcome.error_message or "任务执行失败。",
            )
            await self._publish_event(
                "task.failed",
                {
                    "status": TASK_STATUS_FAILED,
                    "error_message": outcome.error_message or "任务执行失败。",
                },
            )
            return stored
        result_text = outcome.result_text or "任务已完成。"
        stored = await task_lifecycle.save_success(task_id, result_text)
        await self._publish_event(
            "task.message.completed",
            {
                "text": result_text,
                "status": TASK_STATUS_SUCCESS,
            },
        )
        await self._publish_event(
            "task.completed",
            {
                "status": TASK_STATUS_SUCCESS,
                "result_preview": truncate(result_text, limit=500),
            },
        )
        return stored

    async def _load_pending_task(self, task_id: str) -> Any:
        """执行 加载 pending task 的内部辅助逻辑。

        Args:
            task_id: task_id 参数。
        """
        if self.task_lifecycle is not None:
            return await self.task_lifecycle.load_pending(task_id)
        from runtime.compat import load_pending_task

        return await load_pending_task(
            self.session,
            task_id=task_id,
            pending_status=TASK_STATUS_PENDING,
            not_pending_error=NonPendingTaskExecutionError,
            not_found_error=AgentHarnessError,
        )

    async def _load_user(self, user_id: str) -> Any:
        """执行 加载 user 的内部辅助逻辑。

        Args:
            user_id: user_id 参数。
        """
        if self.user_lookup is not None:
            return await self.user_lookup.load_user(user_id)
        from runtime.compat import load_user

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
        """执行 处理 conversation context pack 的内部辅助逻辑。

        Args:
            conversation_id: conversation_id 参数。
            user_id: user_id 参数。
            task_id: task_id 参数。
            current_input: current_input 参数。
            long_term_memory: long_term_memory 参数。
        """
        if self.conversation_context is not None:
            return await self.conversation_context.load_context(
                conversation_id=conversation_id,
                user_id=user_id,
                task_id=task_id,
                current_input=current_input,
                long_term_memory=long_term_memory,
            )

        from runtime.compat import load_conversation_context

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
        """执行 处理 memory context 的内部辅助逻辑。

        Args:
            user_id: user_id 参数。
            query: query 参数。
            task_id: task_id 参数。
            conversation_id: conversation_id 参数。
        """
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
    """执行 处理 default skills root 的内部辅助逻辑。"""
    return Path(__file__).resolve().parents[1] / "resources" / "skillpacks"



__all__ = ["AgentHarness"]
