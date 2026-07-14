from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from assistant_api.config import Settings
from assistant_api.models import Base, Task, TaskStatus, ToolLog, User
from assistant_api.worker_runtime import execute_task_by_id
from packages.agent_harness import (
    AgentDecision,
    AgentModelRequest,
    AgentRunInput,
    AgentRunResult,
    ReviewDecision,
    WorkPlan,
    WorkPlanStep,
)
from packages.tools import (
    MCPToolAdapter,
    MCPToolDescription,
    SearchWebTool,
    TavilySearchRequest,
    ToolApprovalRequiredError,
    ToolInvocation,
    ToolNotAllowedError,
    ToolRegistry,
    ToolSpec,
    build_search_tool_spec,
    build_tavily_config,
)


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v2-execution.db",
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def execution_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///unused.db",
        tavily_base_url="https://tavily.invalid",
        tavily_api_key="placeholder-tavily-key",
        tavily_max_results=3,
    )


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    task_type: str,
    input_text: str | None = None,
) -> Task:
    async with sessionmaker() as session:
        user = User(display_name=f"V2 {task_type} user")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type=task_type,
            input_text=input_text or f"/{task_type} V2 execution protocol",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.commit()
        return task


async def fetch_logs(
    sessionmaker: async_sessionmaker[AsyncSession],
    task_id: str,
) -> list[ToolLog]:
    async with sessionmaker() as session:
        result = await session.scalars(
            select(ToolLog)
            .where(ToolLog.task_id == task_id)
            .order_by(ToolLog.created_at, ToolLog.id)
        )
        return list(result)


class RecordingAgentExecutor:
    def __init__(self) -> None:
        self.inputs: list[AgentRunInput] = []

    async def execute(self, *, run_input: AgentRunInput) -> AgentRunResult:
        self.inputs.append(run_input)
        return AgentRunResult(
            result_text=f"executed {run_input.context.task_type}",
            loop_steps=1,
        )


class FakeTavilyClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[TavilySearchRequest] = []

    async def search(self, request: TavilySearchRequest) -> dict[str, Any]:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return {
            "results": [
                {
                    "title": "LangGraph official guide",
                    "url": "https://example.invalid/langgraph",
                    "content": "StateGraph execution source",
                    "score": 0.95,
                }
            ]
        }


class SearchThenAnswerModel:
    def __init__(self) -> None:
        self.requests: list[AgentModelRequest] = []

    async def create_plan(self, request: AgentModelRequest) -> WorkPlan:
        return WorkPlan(
            goal=request.messages[1].content,
            steps=(
                WorkPlanStep(
                    objective="搜索资料",
                    acceptance_criteria=("获得可核验来源",),
                ),
                WorkPlanStep(
                    objective="形成答案",
                    acceptance_criteria=("回答任务目标",),
                ),
            ),
        )

    async def decide(self, request: AgentModelRequest) -> AgentDecision:
        self.requests.append(request)
        if len(self.requests) == 1:
            return AgentDecision(
                action="tool_call",
                tool_name="search.web",
                arguments={"query": "LangGraph StateGraph"},
            )
        return AgentDecision(
            action="final",
            answer="LangGraph official guide 提供了 StateGraph 执行资料。",
        )

    async def review(self, request: AgentModelRequest) -> ReviewDecision:
        return ReviewDecision(status="pass", feedback="满足验收标准")


def memory_saver() -> InMemorySaver:
    return InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=None)
    )


@pytest.mark.asyncio
async def test_01_worker_uses_structured_agent_run_input_for_primary_tasks(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    for task_type in ("plan", "learn", "daily", "office"):
        task = await create_task(sessionmaker, task_type=task_type)
        executor = RecordingAgentExecutor()

        result = await execute_task_by_id(
            task.id,
            sessionmaker=sessionmaker,
            settings=execution_settings(),
            langgraph_executor=executor,
            tavily_client=FakeTavilyClient(),
        )

        assert result.status == TaskStatus.SUCCESS.value
        assert len(executor.inputs) == 1
        run_input = executor.inputs[0]
        assert run_input.context.task_id == task.id
        assert run_input.context.task_type == task_type
        assert run_input.plan.profile_name.startswith("v2.")
        assert run_input.plan.executor_kind == "langgraph"


@pytest.mark.asyncio
async def test_02_real_langgraph_search_is_bounded_and_every_step_is_audited(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(
        sessionmaker,
        task_type="learn",
        input_text="/learn LangGraph StateGraph",
    )
    tavily = FakeTavilyClient()

    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=execution_settings(),
        tavily_client=tavily,
        agent_model=SearchThenAnswerModel(),
        checkpointer=memory_saver(),
    )
    logs = await fetch_logs(sessionmaker, task.id)
    step_logs = [log for log in logs if log.tool_name.startswith("langgraph.step.")]
    search_logs = [log for log in logs if log.tool_name == "search.web"]

    assert result.status == TaskStatus.SUCCESS.value
    assert result.result_text is not None
    assert "LangGraph official guide" in result.result_text
    assert len(tavily.calls) == 1
    assert [log.tool_name for log in step_logs] == [
        "langgraph.step.prepare",
        "langgraph.step.plan",
        "langgraph.step.model",
        "langgraph.step.tool",
        "langgraph.step.model",
        "langgraph.step.review",
        "langgraph.step.finalize",
    ]
    assert all(log.status == "succeeded" for log in step_logs)
    assert len(step_logs) <= 12
    assert len(search_logs) == 1
    assert search_logs[0].status == "succeeded"


@pytest.mark.asyncio
async def test_03_real_langgraph_failure_is_sanitized_and_step_is_audited(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, task_type="learn")
    secret = execution_settings().tavily_api_key
    unsafe_error = RuntimeError(
        f"Traceback Authorization: Bearer {secret} cookie=session-secret"
    )

    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=execution_settings(),
        tavily_client=FakeTavilyClient(error=unsafe_error),
        agent_model=SearchThenAnswerModel(),
        checkpointer=memory_saver(),
    )
    logs = await fetch_logs(sessionmaker, task.id)
    failed_text = "\n".join(
        value
        for log in logs
        for value in (log.error_message, log.output_text)
        if value
    )

    assert result.status == TaskStatus.FAILED.value
    assert result.error_message is not None
    assert any(
        log.tool_name == "langgraph.step.tool" and log.status == "failed"
        for log in logs
    )
    for text in (result.error_message, failed_text):
        assert secret not in text
        assert "Bearer " not in text
        assert "cookie=" not in text.lower()
        assert "traceback" not in text.lower()


@pytest.mark.asyncio
async def test_04_registry_rejects_disallowed_and_holds_l3_without_execution(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, task_type="plan")
    calls: list[str] = []

    async def handler(invocation: ToolInvocation) -> dict[str, bool]:
        calls.append(invocation.name)
        return {"executed": True}

    async with sessionmaker() as session:
        registry = ToolRegistry(session=session)
        registry.register(
            ToolSpec(
                name="shell.exec",
                description="Disabled shell",
                risk_level="L3",
                handler=handler,
            )
        )
        registry.register(
            ToolSpec(
                name="email.send",
                description="Approval-gated email",
                risk_level="L3",
                handler=handler,
            )
        )

        with pytest.raises(ToolNotAllowedError):
            await registry.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=task.user_id,
                    name="shell.exec",
                ),
                allowed_tools=(),
                approval_required_tools=(),
            )
        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=task.user_id,
                    name="email.send",
                ),
                allowed_tools=(),
                approval_required_tools=("email.send",),
            )
        await session.commit()

    logs = await fetch_logs(sessionmaker, task.id)
    assert calls == []
    assert [(log.tool_name, log.status) for log in logs] == [
        ("shell.exec", "failed"),
        ("email.send", "waiting_approval"),
    ]


@pytest.mark.asyncio
async def test_05_registered_search_writes_exactly_one_tool_log(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, task_type="learn")
    tavily = FakeTavilyClient()

    async with sessionmaker() as session:
        search_tool = SearchWebTool(
            client=tavily,
            session=session,
            config=build_tavily_config(execution_settings()),
        )
        registry = ToolRegistry(session=session)
        registry.register(build_search_tool_spec(search_tool))
        await registry.execute(
            ToolInvocation(
                task_id=task.id,
                user_id=task.user_id,
                name="search.web",
                arguments={"query": "LangGraph tool registry"},
            ),
            allowed_tools=("search.web",),
            approval_required_tools=(),
        )
        await session.commit()

    logs = await fetch_logs(sessionmaker, task.id)
    assert len(tavily.calls) == 1
    assert [(log.tool_name, log.status) for log in logs] == [
        ("search.web", "succeeded")
    ]


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return {"ok": True}


def test_06_mcp_adapter_is_disabled_by_default_and_does_not_connect() -> None:
    client = FakeMCPClient()
    adapter = MCPToolAdapter(client)

    spec = adapter.to_tool_spec(
        MCPToolDescription(name="mcp.files.read", description="Read a file")
    )

    assert spec.enabled is False
    assert client.calls == []


def test_07_readme_documents_completed_v2_03_execution_layer() -> None:
    readme = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")

    assert "V2-03 在 V2-02 规划层上" in readme
    assert "ToolRegistry" in readme
    assert "MCP Server" in readme
    assert "默认不启用" in readme
    assert "LangGraph" in readme
    assert "V3-08 已移除 Deepeval" in readme
    assert "V2-05 评测与回归阶段" in readme
