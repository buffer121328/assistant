from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent import (
    AgentHarness,
    AgentModelProtocol,
    ExecutionBoundary,
    LangGraphExecutor,
    GovernedEvolutionService,
    ManagedSkillStore,
    SubAgentCoordinator,
)
from memory import Mem0MemoryAdapter
from agent.skill_management.acquisition import SkillAcquisitionService
from agent.skill_management.lifecycle import SkillLifecycleService
from agent.prompting import PromptBuilder, PromptStore
from observability import Observability
from rag import KnowledgeService
from integrations import (
    AccountBackedProviders,
    AccountBackedBrowserSessions,
    CredentialCipher,
    CredentialError,
    active_connection_providers,
)
from agent.review import JudgeModel, QualityEvaluator, SamplingPolicy
from capabilities import CapabilityRegistry, build_default_registry
from tools import (
    AgentScheduleService,
    AgentTaskToolService,
    AgentMemoryToolService,
    PromptToolService,
    ArtifactStore,
    DockerSandboxConfig,
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
    build_knowledge_tool_descriptor,
    build_knowledge_tool_spec,
    ReadonlyShellRunner,
    WorkspaceContextStore,
    build_workspace_tool_descriptors,
    build_workspace_tool_specs,
    parse_deny_globs,
    BrowserInteractor,
    build_browser_tool_descriptors,
    build_browser_tool_specs,
    build_sandbox_runner,
    build_schedule_tool_descriptors,
    build_schedule_tool_specs,
    build_memory_tool_descriptors,
    build_memory_tool_specs,
    build_prompt_tool_descriptors,
    build_prompt_tool_specs,
    build_skill_tool_descriptors,
    build_skill_tool_specs,
    build_task_tool_descriptors,
    build_task_tool_specs,
    build_search_provider_chain,
    build_tavily_config,
)

from infrastructure.config import Settings
from models.agent_model import AgentGatewayModel
from infrastructure.agent_ports import (
    SqlAlchemyConversationContextPort,
    SqlAlchemyExecutionTracePort,
    SqlAlchemyLocalTaskServicePort,
    SqlAlchemyTaskLifecyclePort,
    SqlAlchemyUserLookupPort,
)
from infrastructure.checkpoints import open_agent_checkpointer
from agent.governance.capability_routing import (
    CapabilityRoutingService,
    RoutingModelAdapter,
)
from channels.langbot.service import LangBotResultClient
from domain.models import EvolutionChange, Task, TaskStatus
from infrastructure.observability import build_observability
from agent.review.gateway import GatewayJudgeModel
from application.dispatch import ResultDispatcher
from policies.task_status import DISPATCHABLE_TASK_STATUSES
from runtime.subagent_gateway import GatewaySubAgentRunner
from application.task_events import TASK_EVENT_STATUS, TaskEventPublisher
from workers.composition.lifecycle import (
    finish_agent_run as _finish_agent_run,
    record_worker_failure as _record_worker_failure,
    sanitize_failed_task as _sanitize_failed_task,
    sensitive_values as _sensitive_values,
    start_agent_run as _start_agent_run,
)

BUILTIN_SKILL_ROOT = Path(__file__).resolve().parents[1] / "resources" / "skillpacks"


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
    """执行 task by id。

    Args:
        task_id: task_id 参数。
        sessionmaker: sessionmaker 参数。
        settings: settings 参数。
        langgraph_executor: langgraph_executor 参数。
        tavily_client: tavily_client 参数。
        langbot_client: langbot_client 参数。
        routing_adapter: routing_adapter 参数。
        capability_registry: capability_registry 参数。
        agent_model: agent_model 参数。
        checkpointer: checkpointer 参数。
        observability: observability 参数。
        judge_model: judge_model 参数。
    """
    sensitive_values = _sensitive_values(settings)
    runtime_observability = observability or build_observability(settings)
    owns_observability = observability is None
    try:
        async with sessionmaker() as session:
            task_preview = await session.get(Task, task_id)
            agent_run = (
                await _start_agent_run(session, task_preview)
                if task_preview is not None
                else None
            )
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
                        agent_run_id=agent_run.id if agent_run is not None else None,
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

                publisher = TaskEventPublisher(sessionmaker)
                await publisher.publish(
                    task_id=task.id,
                    user_id=task.user_id,
                    event_type=TASK_EVENT_STATUS,
                    payload={"status": task.status},
                )

                if agent_run is not None:
                    await _finish_agent_run(
                        session,
                        agent_run=agent_run,
                        task=task,
                        sensitive_values=sensitive_values,
                    )

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
    agent_run_id: str | None,
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
    """执行 执行 with runtime dependencies 的内部辅助逻辑。

    Args:
        task_id: task_id 参数。
        agent_run_id: agent_run_id 参数。
        session: session 参数。
        settings: settings 参数。
        sensitive_values: sensitive_values 参数。
        langgraph_executor: langgraph_executor 参数。
        tavily_client: tavily_client 参数。
        routing_adapter: routing_adapter 参数。
        capability_registry: capability_registry 参数。
        agent_model: agent_model 参数。
        checkpointer: checkpointer 参数。
        observability: observability 参数。
        sessionmaker: sessionmaker 参数。
    """
    if langgraph_executor is not None:
        return await _execute_with_harness(
            task_id,
            agent_run_id=agent_run_id,
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
            agent_run_id=agent_run_id,
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
            agent_run_id=agent_run_id,
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
    agent_run_id: str | None,
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
    """执行 执行 with harness 的内部辅助逻辑。

    Args:
        task_id: task_id 参数。
        agent_run_id: agent_run_id 参数。
        session: session 参数。
        settings: settings 参数。
        sensitive_values: sensitive_values 参数。
        langgraph_executor: langgraph_executor 参数。
        tavily_client: tavily_client 参数。
        routing_adapter: routing_adapter 参数。
        capability_registry: capability_registry 参数。
        agent_model: agent_model 参数。
        checkpointer: checkpointer 参数。
        observability: observability 参数。
        sessionmaker: sessionmaker 参数。
    """
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
                builtin_root=(BUILTIN_SKILL_ROOT),
                managed_root=settings.managed_skills_root,
            ),
            skill_package_root=settings.skill_packages_root,
        )
        for change in pending_changes:
            await governed.apply(change_id=change.id, user_id=change.user_id)

    task = await session.get(Task, task_id)
    if task is not None and task.task_type == "agent":
        # fmt: off
        registry = capability_registry or build_default_registry(
            BUILTIN_SKILL_ROOT
        )
        # fmt: on
        await CapabilityRoutingService(
            session=session,
            settings=settings,
            registry=registry,
            adapter=routing_adapter,
            agent_run_id=agent_run_id,
        ).route_task(task)

    tavily_config = build_tavily_config(settings)
    search_provider_chain = build_search_provider_chain(
        tavily_config,
        tavily_client=tavily_client,
        sensitive_values=sensitive_values,
    )
    search_tool = SearchWebTool(
        session=session,
        config=tavily_config,
        client=tavily_client or TavilyApiClient(tavily_config),
        provider_chain=search_provider_chain,
        sensitive_values=sensitive_values,
    )
    search_descriptor = build_search_tool_descriptor(enabled=True)
    knowledge_descriptor = build_knowledge_tool_descriptor()
    workspace_context: WorkspaceContextStore | None = None
    workspace_context_available = False
    try:
        workspace_context = WorkspaceContextStore(
            root=settings.workspace_context_root,
            deny_globs=parse_deny_globs(settings.workspace_context_deny_globs),
            max_file_bytes=settings.workspace_context_max_file_bytes,
            max_results=settings.workspace_context_max_results,
            sensitive_values=sensitive_values,
        )
        workspace_context_available = (
            settings.workspace_context_enabled and workspace_context.available
        )
    except Exception:
        workspace_context = None
        workspace_context_available = False
    readonly_shell = (
        ReadonlyShellRunner(
            store=workspace_context,
            enabled=settings.readonly_shell_enabled,
            timeout_seconds=settings.readonly_shell_timeout_seconds,
            max_output_chars=settings.readonly_shell_max_output_chars,
        )
        if workspace_context is not None
        else None
    )
    workspace_descriptors = build_workspace_tool_descriptors(
        enabled=workspace_context_available,
        readonly_shell_enabled=bool(readonly_shell and readonly_shell.available),
    )
    sandbox = build_sandbox_runner(
        provider=settings.effective_sandbox_provider,
        docker_config=DockerSandboxConfig(
            enabled=settings.effective_shell_exec_enabled,
            image=settings.effective_sandbox_docker_image,
            allowed_images=settings.effective_sandbox_docker_allowed_images_tuple,
            timeout_seconds=settings.sandbox_timeout_seconds,
        ),
        workspace_root=settings.sandbox_workspace_root,
    )
    external_providers: AccountBackedProviders | None = None
    credential_cipher: CredentialCipher | None = None
    active_providers: frozenset[str] = frozenset()
    if task is not None:
        try:
            cipher = CredentialCipher(settings.credential_master_key.get_secret_value())
        except CredentialError:
            pass
        else:
            credential_cipher = cipher
            active_providers = await active_connection_providers(session, task.user_id)
            external_providers = AccountBackedProviders(session, cipher=cipher)
    personal_descriptors = build_personal_tool_descriptors(
        browser_available=settings.browser_enabled,
        sandbox_available=sandbox.available,
        email_provider_available="smtp" in active_providers,
        calendar_provider_available="caldav" in active_providers,
    )
    browser_interaction_descriptors = build_browser_tool_descriptors(
        enabled=(
            settings.browser_enabled
            and "browser" in active_providers
            and credential_cipher is not None
        )
    )
    skill_descriptors = build_skill_tool_descriptors(enabled=True)
    task_descriptors = build_task_tool_descriptors(enabled=True)
    schedule_descriptors = build_schedule_tool_descriptors(enabled=True)
    memory_descriptors = build_memory_tool_descriptors(enabled=True)
    prompt_descriptors = build_prompt_tool_descriptors(enabled=True)
    tool_catalog = ToolCatalog(
        (
            StaticToolSource(
                "builtin",
                (
                    search_descriptor,
                    knowledge_descriptor,
                    *workspace_descriptors,
                    *personal_descriptors,
                    *browser_interaction_descriptors,
                    *skill_descriptors,
                    *task_descriptors,
                    *schedule_descriptors,
                    *memory_descriptors,
                    *prompt_descriptors,
                ),
            ),
        ),
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
    tool_registry.register(
        build_knowledge_tool_spec(
            KnowledgeService(session, import_root=settings.knowledge_root)
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
    browser_interactor = (
        BrowserInteractor(
            sessions=AccountBackedBrowserSessions(session, cipher=credential_cipher),
            timeout_seconds=settings.browser_timeout_seconds,
            max_text_chars=settings.browser_max_text_chars,
        )
        if (
            settings.browser_enabled
            and "browser" in active_providers
            and credential_cipher is not None
        )
        else None
    )
    workspace_specs = (
        build_workspace_tool_specs(
            store=workspace_context,
            readonly_shell=readonly_shell,
        )
        if workspace_context is not None
        else ()
    )
    personal_specs = build_personal_tool_specs(
        productivity=productivity,
        browser=browser,
        sandbox=sandbox,
        email_provider=(external_providers if "smtp" in active_providers else None),
        calendar_provider=(
            external_providers if "caldav" in active_providers else None
        ),
    )
    browser_specs = (
        build_browser_tool_specs(browser_interactor)
        if browser_interactor is not None
        else ()
    )
    skill_store = ManagedSkillStore(
        builtin_root=BUILTIN_SKILL_ROOT,
        managed_root=settings.managed_skills_root,
    )

    def refresh_skill_registry() -> None:
        """处理 refresh skill registry。"""
        return None

    skill_specs = build_skill_tool_specs(
        SkillAcquisitionService(
            lifecycle=SkillLifecycleService(
                session,
                store=skill_store,
                refresh_registry=refresh_skill_registry,
            )
        )
    )

    def enqueue_background_task(background_task_id: str) -> bool:
        """处理 enqueue background task。

        Args:
            background_task_id: background_task_id 参数。
        """
        from workers.worker import enqueue_task_execution

        return enqueue_task_execution(
            background_task_id,
            runtime_settings=settings,
        )

    task_specs = build_task_tool_specs(
        AgentTaskToolService(session, enqueue_task=enqueue_background_task)
    )
    schedule_specs = build_schedule_tool_specs(AgentScheduleService(session))
    semantic_memory = Mem0MemoryAdapter(settings.mem0_config_path)
    memory_specs = build_memory_tool_specs(
        AgentMemoryToolService(session=session, semantic_memory=semantic_memory)
    )
    prompt_store = PromptStore(
        defaults_root=Path(__file__).resolve().parents[1]
        / "resources"
        / "prompts"
        / "defaults",
        managed_root=settings.managed_prompts_root,
    )
    prompt_specs = build_prompt_tool_specs(
        PromptToolService(session=session, store=prompt_store)
    )
    for spec in (
        *workspace_specs,
        *personal_specs,
        *browser_specs,
        *skill_specs,
        *task_specs,
        *schedule_specs,
        *memory_specs,
        *prompt_specs,
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
    event_publisher = TaskEventPublisher(sessionmaker)

    async def publish_event(event_type: str, payload: dict[str, object]) -> None:
        """发布 event。

        Args:
            event_type: event_type 参数。
            payload: payload 参数。
        """
        current = await session.get(Task, task_id)
        if current is not None:
            await event_publisher.publish(
                task_id=current.id,
                user_id=current.user_id,
                event_type=event_type,
                payload=payload,
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
                agent_run_id=agent_run_id,
                observability=observability,
                event_sink=publish_event,
            ),
            checkpointer=checkpointer,
            sensitive_values=sensitive_values,
            tool_snapshot=tool_snapshot,
            observability=observability,
            prompt_builder=PromptBuilder(prompt_store),
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
        trace=SqlAlchemyExecutionTracePort(session),
    )
    return await AgentHarness(
        session=session,
        executor=executor,
        search_tool=search_tool,
        tool_snapshot=tool_snapshot,
        semantic_memory=semantic_memory,
        semantic_memory_limit=settings.mem0_search_limit,
        event_sink=publish_event,
        task_lifecycle=SqlAlchemyTaskLifecyclePort(session),
        local_tasks=SqlAlchemyLocalTaskServicePort(
            session,
            semantic_memory=semantic_memory,
        ),
        conversation_context=SqlAlchemyConversationContextPort(session),
        user_lookup=SqlAlchemyUserLookupPort(session),
    ).execute_task(task_id)
