from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.agent_harness import (
    AgentHarness,
    AgentModelProtocol,
    ExecutionBoundary,
    LangGraphExecutor,
    GovernedEvolutionService,
    ManagedSkillStore,
    SubAgentCoordinator,
)
from packages.model_gateway import sanitize_text
from packages.memory import Mem0MemoryAdapter
from packages.observability import Observability
from packages.quality import JudgeModel, QualityEvaluator, SamplingPolicy
from packages.capabilities import CapabilityRegistry, build_default_registry
from packages.tools import (
    ArtifactStore,
    DockerSandboxConfig,
    DockerSandboxRunner,
    PlaywrightBrowserReader,
    ProductivityTools,
    SearchWebTool,
    StaticToolSource,
    TavilyApiClient,
    ToolCatalog,
    ToolRegistry,
    ToolSpec,
    build_search_tool_descriptor,
    build_search_tool_spec,
    build_personal_tool_descriptors,
    build_personal_tool_specs,
    build_tavily_config,
)

from .config import Settings
from .agent_model import AgentGatewayModel
from .checkpoints import open_agent_checkpointer
from .agent_routing import CapabilityRoutingService, RoutingModelAdapter
from .langbot import LangBotResultClient
from .models import EvolutionChange, Task, TaskStatus
from .observability import build_observability
from .quality import GatewayJudgeModel
from .services import DISPATCHABLE_TASK_STATUSES, ResultDispatcher
from .subagents import GatewaySubAgentRunner


async def execute_task_by_id(
    task_id: str,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    langgraph_executor: Any | None = None,
    tavily_client: Any | None = None,
    langbot_client: Any | None = None,
    routing_adapter: RoutingModelAdapter | None = None,
    capability_registry: CapabilityRegistry | None = None,
    agent_model: AgentModelProtocol | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    observability: Observability | None = None,
    judge_model: JudgeModel | None = None,
) -> Task:
    sensitive_values = _sensitive_values(settings)
    runtime_observability = observability or build_observability(settings)
    owns_observability = observability is None
    try:
        async with sessionmaker() as session:
            task_preview = await session.get(Task, task_id)
            with runtime_observability.observe(
                "agent.task",
                as_type="agent",
                input={"task_id": task_id},
                metadata={
                    "task_id": task_id,
                    "task_type": task_preview.task_type if task_preview else None,
                    "user_id": task_preview.user_id if task_preview else None,
                },
            ) as observation:
                try:
                    task = await _execute_with_runtime_dependencies(
                        task_id=task_id,
                        session=session,
                        settings=settings,
                        sensitive_values=sensitive_values,
                        langgraph_executor=langgraph_executor,
                        tavily_client=tavily_client,
                        routing_adapter=routing_adapter,
                        capability_registry=capability_registry,
                        agent_model=agent_model,
                        checkpointer=checkpointer,
                        observability=runtime_observability,
                        sessionmaker=sessionmaker,
                    )
                    task = await _sanitize_failed_task(
                        session,
                        task=task,
                        sensitive_values=sensitive_values,
                    )
                    if (
                        task.status == TaskStatus.SUCCESS.value
                        and 0.0 < settings.quality_judge_sample_rate <= 1.0
                    ):
                        await QualityEvaluator(
                            sampling=SamplingPolicy(
                                rate=settings.quality_judge_sample_rate,
                                version=settings.quality_judge_policy_version,
                            ),
                            judge=judge_model
                            or GatewayJudgeModel(
                                session=session,
                                settings=settings,
                            ),
                            observability=runtime_observability,
                            threshold=settings.quality_judge_threshold,
                        ).evaluate_task(session=session, task=task)
                        await session.refresh(task)
                except Exception as exc:
                    task = await _record_worker_failure(
                        session,
                        task_id=task_id,
                        error=exc,
                        sensitive_values=sensitive_values,
                    )

                if (
                    task.status in DISPATCHABLE_TASK_STATUSES
                    and task.platform == "langbot"
                ):
                    dispatcher = ResultDispatcher(
                        session,
                        langbot_client=langbot_client or LangBotResultClient(settings),
                        sensitive_values=sensitive_values,
                    )
                    await dispatcher.dispatch_task(task.id)
                    await session.refresh(task)

                observation.update(
                    output={"status": task.status},
                    metadata={"task_id": task.id},
                )
                return task
    finally:
        runtime_observability.flush()
        if owns_observability:
            runtime_observability.shutdown()


async def _execute_with_runtime_dependencies(
    *,
    task_id: str,
    session: AsyncSession,
    settings: Settings,
    sensitive_values: tuple[str | None, ...],
    langgraph_executor: Any | None,
    tavily_client: Any | None,
    routing_adapter: RoutingModelAdapter | None,
    capability_registry: CapabilityRegistry | None,
    agent_model: AgentModelProtocol | None,
    checkpointer: BaseCheckpointSaver | None,
    observability: Observability,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> Task:
    if langgraph_executor is not None:
        return await _execute_with_harness(
            task_id,
            session=session,
            settings=settings,
            sensitive_values=sensitive_values,
            langgraph_executor=langgraph_executor,
            tavily_client=tavily_client,
            routing_adapter=routing_adapter,
            capability_registry=capability_registry,
            agent_model=agent_model,
            checkpointer=None,
            observability=observability,
            sessionmaker=sessionmaker,
        )
    if checkpointer is not None:
        return await _execute_with_harness(
            task_id,
            session=session,
            settings=settings,
            sensitive_values=sensitive_values,
            langgraph_executor=None,
            tavily_client=tavily_client,
            routing_adapter=routing_adapter,
            capability_registry=capability_registry,
            agent_model=agent_model,
            checkpointer=checkpointer,
            observability=observability,
            sessionmaker=sessionmaker,
        )
    async with open_agent_checkpointer(settings.database_url) as runtime_checkpointer:
        return await _execute_with_harness(
            task_id,
            session=session,
            settings=settings,
            sensitive_values=sensitive_values,
            langgraph_executor=None,
            tavily_client=tavily_client,
            routing_adapter=routing_adapter,
            capability_registry=capability_registry,
            agent_model=agent_model,
            checkpointer=runtime_checkpointer,
            observability=observability,
            sessionmaker=sessionmaker,
        )


async def _execute_with_harness(
    task_id: str,
    *,
    session: AsyncSession,
    settings: Settings,
    sensitive_values: tuple[str | None, ...],
    langgraph_executor: Any | None,
    tavily_client: Any | None,
    routing_adapter: RoutingModelAdapter | None,
    capability_registry: CapabilityRegistry | None,
    agent_model: AgentModelProtocol | None,
    checkpointer: BaseCheckpointSaver | None,
    observability: Observability,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> Task:
    pending_changes = list(
        await session.scalars(
            select(EvolutionChange).where(
                EvolutionChange.task_id == task_id,
                EvolutionChange.status == "pending",
            )
        )
    )
    if pending_changes:
        governed = GovernedEvolutionService(
            session=session,
            prompt_root=settings.managed_prompts_root,
            skill_root=settings.managed_skills_root,
            skill_store=ManagedSkillStore(
                builtin_root=(
                    Path(__file__).resolve().parents[3] / "prompts" / "skills"
                ),
                managed_root=settings.managed_skills_root,
            ),
            skill_package_root=settings.skill_packages_root,
        )
        for change in pending_changes:
            await governed.apply(change_id=change.id, user_id=change.user_id)

    task = await session.get(Task, task_id)
    if task is not None and task.task_type == "agent":
        registry = capability_registry or build_default_registry(
            Path(__file__).resolve().parents[3] / "prompts" / "skills"
        )
        await CapabilityRoutingService(
            session=session,
            settings=settings,
            registry=registry,
            adapter=routing_adapter,
        ).route_task(task)

    tavily_config = build_tavily_config(settings)
    search_tool = SearchWebTool(
        client=tavily_client or TavilyApiClient(tavily_config),
        session=session,
        config=tavily_config,
        sensitive_values=sensitive_values,
    )
    search_descriptor = build_search_tool_descriptor(enabled=True)
    sandbox = DockerSandboxRunner(
        config=DockerSandboxConfig(
            enabled=settings.sandbox_enabled,
            image=settings.sandbox_image,
            allowed_images=tuple(
                item.strip()
                for item in settings.sandbox_allowed_images.split(",")
                if item.strip()
            ),
            timeout_seconds=settings.sandbox_timeout_seconds,
        ),
        workspace_root=settings.sandbox_workspace_root,
    )
    personal_descriptors = build_personal_tool_descriptors(
        browser_available=settings.browser_enabled,
        sandbox_available=sandbox.available,
    )
    tool_catalog = ToolCatalog(
        (StaticToolSource("builtin", (search_descriptor, *personal_descriptors)),),
        sensitive_values=sensitive_values,
    )
    tool_snapshot = await tool_catalog.refresh()
    tool_registry = ToolRegistry(
        session=session,
        sensitive_values=sensitive_values,
        snapshot_revision=tool_snapshot.revision,
    )
    tool_registry.register(
        build_search_tool_spec(
            search_tool,
            version=search_descriptor.version,
            source_id=search_descriptor.source_id,
            source_available=tool_snapshot.is_available(search_descriptor),
        )
    )
    productivity = ProductivityTools(ArtifactStore(settings.artifacts_root))
    browser = (
        PlaywrightBrowserReader(
            timeout_seconds=settings.browser_timeout_seconds,
            max_text_chars=settings.browser_max_text_chars,
        )
        if settings.browser_enabled
        else None
    )
    for spec in build_personal_tool_specs(
        productivity=productivity,
        browser=browser,
        sandbox=sandbox,
    ):
        descriptor = tool_snapshot.get(spec.name)
        if descriptor is None or not descriptor.enabled:
            continue
        tool_registry.register(
            ToolSpec(
                name=spec.name,
                description=spec.description,
                risk_level=spec.risk_level,
                handler=spec.handler,
                enabled=spec.enabled,
                handler_records_log=spec.handler_records_log,
                input_schema=spec.input_schema,
                version=descriptor.version,
                source_id=descriptor.source_id,
                source_available=tool_snapshot.is_available(descriptor),
                parallel_safe=spec.parallel_safe,
            )
        )
    runtime_executor = langgraph_executor
    if runtime_executor is None:
        if checkpointer is None:
            raise RuntimeError("Agent checkpoint is unavailable")
        runtime_executor = LangGraphExecutor(
            session=session,
            tool_registry=tool_registry,
            model=agent_model
            or AgentGatewayModel(
                session=session,
                settings=settings,
                observability=observability,
            ),
            checkpointer=checkpointer,
            sensitive_values=sensitive_values,
            tool_snapshot=tool_snapshot,
            observability=observability,
            subagent_coordinator=(
                SubAgentCoordinator(
                    runner=GatewaySubAgentRunner(
                        sessionmaker=sessionmaker,
                        settings=settings,
                        observability=observability,
                    ),
                    max_subagents=settings.subagent_max_count,
                    concurrency=settings.subagent_concurrency,
                    timeout_seconds=settings.subagent_timeout_seconds,
                )
                if settings.subagent_enabled
                else None
            ),
        )
    executor = ExecutionBoundary(
        session=session,
        langgraph_executor=runtime_executor,
        sensitive_values=sensitive_values,
    )
    return await AgentHarness(
        session=session,
        executor=executor,
        search_tool=search_tool,
        tool_snapshot=tool_snapshot,
        semantic_memory=Mem0MemoryAdapter(settings.mem0_config_path),
        semantic_memory_limit=settings.mem0_search_limit,
    ).execute_task(task_id)


async def _record_worker_failure(
    session: AsyncSession,
    *,
    task_id: str,
    error: Exception,
    sensitive_values: tuple[str | None, ...],
) -> Task:
    await session.rollback()
    task = await session.get(Task, task_id)
    if task is None:
        raise error

    if task.status not in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}:
        return task

    if task.status == TaskStatus.PENDING.value:
        task.status = TaskStatus.RUNNING.value
        task.error_message = None
        task.result_text = None
        await session.flush()

    task.status = TaskStatus.FAILED.value
    task.result_text = None
    task.error_message = _safe_worker_summary(error, sensitive_values=sensitive_values)
    await session.commit()
    await session.refresh(task)
    return task


async def _sanitize_failed_task(
    session: AsyncSession,
    *,
    task: Task,
    sensitive_values: tuple[str | None, ...],
) -> Task:
    if task.status != TaskStatus.FAILED.value or task.error_message is None:
        return task

    safe_error = _safe_worker_summary(
        task.error_message,
        sensitive_values=sensitive_values,
    )
    if safe_error == task.error_message:
        return task

    task.error_message = safe_error
    await session.commit()
    await session.refresh(task)
    return task


def _safe_worker_summary(
    value: object,
    *,
    sensitive_values: tuple[str | None, ...],
    limit: int = 1000,
) -> str:
    text = sanitize_text(value, extra_sensitive_values=sensitive_values).strip()
    if "traceback" in text.lower():
        text = "内部错误已脱敏"
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _sensitive_values(settings: Settings) -> tuple[str | None, ...]:
    return (
        settings.langbot_webhook_secret,
        settings.langbot_api_base_url,
        settings.langbot_api_key,
        settings.tavily_base_url,
        settings.tavily_api_key,
        settings.deepseek_api_key,
    )
