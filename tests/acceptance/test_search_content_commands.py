from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from assistant_api.config import Settings
from assistant_api.main import create_app
from assistant_api.models import Base, Memory, Task, TaskStatus, ToolLog, User
from assistant_api.worker_runtime import execute_task_by_id
from packages.agent_harness import (
    AgentDecision,
    AgentModelRequest,
    ReviewDecision,
    WorkPlan,
    WorkPlanStep,
)
from packages.model_gateway.deepseek import DeepSeekAdapter
from packages.tools import (
    NormalizedSearchSource,
    SearchWebTool,
    TavilyClientError,
    TavilySearchRequest,
    build_tavily_config,
)


TAVILY_API_KEY = "fake-tavily-api-key"
SECRET_TOKEN = "secret-token-value"
PRIVATE_URL = "https://private.example.invalid/search"


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/search-content-commands.db",
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


def content_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///unused.db",
        tavily_base_url="https://tavily.invalid",
        tavily_api_key=TAVILY_API_KEY,
        tavily_timeout_seconds=0.1,
        tavily_max_results=5,
        deepseek_api_key="fake-deepseek-key-that-must-not-be-read",
    )


async def create_user_and_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    task_type: str,
    input_text: str,
    status: TaskStatus = TaskStatus.PENDING,
) -> tuple[User, Task]:
    async with sessionmaker() as session:
        user = User(display_name=f"Search User {task_type}")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type=task_type,
            input_text=input_text,
            status=status.value,
        )
        session.add(task)
        await session.commit()
        return user, task


async def fetch_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    task_id: str,
) -> Task:
    async with sessionmaker() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        return task


async def fetch_tool_logs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> list[ToolLog]:
    async with sessionmaker() as session:
        result = await session.scalars(select(ToolLog).order_by(ToolLog.created_at))
        return list(result)


def assert_no_sensitive_text(value: str | None) -> None:
    assert value is not None
    assert TAVILY_API_KEY not in value
    assert SECRET_TOKEN not in value
    assert PRIVATE_URL not in value
    assert "Bearer " not in value
    assert "authorization" not in value.lower()
    assert "cookie" not in value.lower()


class FakeTavilyClient:
    def __init__(
        self,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.response = response or {"results": []}
        self.error = error
        self.events = events
        self.calls: list[TavilySearchRequest] = []

    async def search(self, request: TavilySearchRequest) -> dict[str, Any]:
        self.calls.append(request)
        if self.events is not None:
            self.events.append("search.web")
        if self.error is not None:
            raise self.error
        return self.response


class ContentAgentModel:
    async def create_plan(self, request: AgentModelRequest) -> WorkPlan:
        return WorkPlan(
            goal=request.messages[1].content,
            steps=(
                WorkPlanStep(
                    objective="检索并核对来源",
                    acceptance_criteria=("来源可核验",),
                ),
                WorkPlanStep(
                    objective="整理结论",
                    acceptance_criteria=("回答任务目标",),
                ),
            ),
        )

    async def decide(self, request: AgentModelRequest) -> AgentDecision:
        combined = "\n".join(message.content for message in request.messages)
        if request.task_type in {"learn", "daily"}:
            if "工具结果 search.web" not in combined:
                return AgentDecision(
                    action="tool_call",
                    tool_name="search.web",
                    arguments={"query": request.messages[1].content},
                )
            if "sources=[]" in combined:
                return AgentDecision(
                    action="final",
                    answer="没有找到可用搜索结果，请调整主题后重试。",
                )
            return AgentDecision(
                action="final",
                answer=(
                    "参考来源:\n"
                    "- Python Agent Guide - https://example.com/python-agent\n"
                    "- Workflow News - https://example.com/workflow-news"
                ),
            )
        return AgentDecision(
            action="final",
            answer="已整理会议纪要。",
        )

    async def review(self, request: AgentModelRequest) -> ReviewDecision:
        return ReviewDecision(status="pass", feedback="满足验收标准")


def memory_saver() -> InMemorySaver:
    return InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=None)
    )


async def execute_content_task(
    task_id: str,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    tavily_client: FakeTavilyClient,
) -> Task:
    return await execute_task_by_id(
        task_id,
        sessionmaker=sessionmaker,
        settings=content_settings(),
        tavily_client=tavily_client,
        agent_model=ContentAgentModel(),
        checkpointer=memory_saver(),
    )


def tavily_sources_response() -> dict[str, Any]:
    return {
        "results": [
            {
                "title": "Python Agent Guide",
                "url": "https://example.com/python-agent",
                "content": "Agent 学习资料摘要",
                "score": 0.92,
                "raw_content": f"Bearer {TAVILY_API_KEY} {PRIVATE_URL}",
            },
            {
                "title": "Python Agent Guide Duplicate",
                "url": "https://example.com/python-agent",
                "content": "重复来源",
                "score": 0.9,
            },
            {
                "title": "Workflow News",
                "url": "https://example.com/workflow-news",
                "snippet": "日报来源摘要",
                "published_date": "2026-06-22",
            },
        ],
        "answer": f"unsafe token={SECRET_TOKEN} {PRIVATE_URL}",
    }


def source_titles(sources: list[NormalizedSearchSource]) -> list[str]:
    return [source.title for source in sources]


@pytest.mark.asyncio
async def test_01_tavily_settings_are_placeholder_safe_and_startup_does_not_connect(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///unused.db")

    assert settings.tavily_base_url == "https://tavily.invalid"
    assert settings.tavily_api_key == "placeholder-tavily-api-key"
    assert settings.tavily_timeout_seconds == 10.0
    assert settings.tavily_max_results == 5
    assert build_tavily_config(settings).base_url == "https://tavily.invalid"

    app = create_app(settings)
    app.state.db_sessionmaker = sessionmaker
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_02_search_web_success_normalizes_deduplicates_and_sanitizes(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_user_and_task(
        sessionmaker,
        task_type="learn",
        input_text="/learn Python Agent",
    )
    client = FakeTavilyClient(tavily_sources_response())

    async with sessionmaker() as session:
        tool = SearchWebTool(
            client=client,
            session=session,
            config=build_tavily_config(content_settings()),
        )
        result = await tool.search(
            task_id=task.id,
            user_id=user.id,
            query=task.input_text,
        )
        logs = list((await session.scalars(select(ToolLog))).all())

    assert client.calls[0].query == task.input_text
    assert source_titles(result.sources) == ["Python Agent Guide", "Workflow News"]
    assert result.sources[0].url == "https://example.com/python-agent"
    assert result.sources[0].snippet == "Agent 学习资料摘要"
    assert result.sources[0].provider_metadata == {
        "provider": "tavily",
        "score": 0.92,
    }
    assert "raw_content" not in str(result.sources)

    assert len(logs) == 1
    assert logs[0].task_id == task.id
    assert logs[0].tool_name == "search.web"
    assert logs[0].status == "succeeded"
    assert logs[0].input_text is not None
    assert logs[0].output_text is not None
    assert_no_sensitive_text(logs[0].input_text)
    assert_no_sensitive_text(logs[0].output_text)


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["learn", "daily"])
async def test_03_search_commands_use_langgraph_and_save_sources(
    sessionmaker: async_sessionmaker[AsyncSession],
    task_type: str,
) -> None:
    _user, task = await create_user_and_task(
        sessionmaker,
        task_type=task_type,
        input_text=f"/{task_type} Python Agent",
    )
    tavily = FakeTavilyClient(tavily_sources_response())

    result = await execute_content_task(
        task.id,
        sessionmaker=sessionmaker,
        tavily_client=tavily,
    )

    stored = await fetch_task(sessionmaker, task.id)
    logs = await fetch_tool_logs(sessionmaker)

    assert result.status == TaskStatus.SUCCESS.value
    assert stored.status == TaskStatus.SUCCESS.value
    assert stored.result_text is not None
    assert "参考来源" in stored.result_text
    assert "https://example.com/python-agent" in stored.result_text
    assert "https://example.com/workflow-news" in stored.result_text
    assert len(tavily.calls) == 1
    assert any(log.tool_name == "search.web" and log.status == "succeeded" for log in logs)


@pytest.mark.asyncio
async def test_05_office_uses_langgraph_without_search_or_files(
    tmp_path: Path,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _user, task = await create_user_and_task(
        sessionmaker,
        task_type="office",
        input_text="/office 整理会议纪要",
    )
    tavily = FakeTavilyClient(
        error=AssertionError("search.web must not be called for office")
    )
    result = await execute_content_task(
        task.id,
        sessionmaker=sessionmaker,
        tavily_client=tavily,
    )

    stored = await fetch_task(sessionmaker, task.id)

    assert tavily.calls == []
    assert result.status == TaskStatus.SUCCESS.value
    assert stored.status == TaskStatus.SUCCESS.value
    assert stored.workflow_key == "langgraph.office"
    assert list(tmp_path.glob("*.docx")) == []
    assert list(tmp_path.glob("*.pptx")) == []
    assert list(tmp_path.glob("*.xlsx")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["learn", "daily"])
async def test_06_required_search_failure_fails_task_safely(
    sessionmaker: async_sessionmaker[AsyncSession],
    task_type: str,
) -> None:
    _user, task = await create_user_and_task(
        sessionmaker,
        task_type=task_type,
        input_text=f"/{task_type} unsafe failure",
    )
    unsafe_error = TavilyClientError(
        f"timeout Bearer {TAVILY_API_KEY} token={SECRET_TOKEN} {PRIVATE_URL}"
    )

    await execute_content_task(
        task.id,
        sessionmaker=sessionmaker,
        tavily_client=FakeTavilyClient(error=unsafe_error),
    )

    stored = await fetch_task(sessionmaker, task.id)
    logs = await fetch_tool_logs(sessionmaker)

    assert stored.status == TaskStatus.FAILED.value
    assert stored.error_message is not None
    assert_no_sensitive_text(stored.error_message)
    search_log = next(log for log in logs if log.tool_name == "search.web")
    assert search_log.status == "failed"
    assert search_log.error_message is not None
    assert_no_sensitive_text(search_log.error_message)


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["learn", "daily"])
async def test_07_empty_search_results_succeed_without_fabricated_sources(
    sessionmaker: async_sessionmaker[AsyncSession],
    task_type: str,
) -> None:
    _user, task = await create_user_and_task(
        sessionmaker,
        task_type=task_type,
        input_text=f"/{task_type} obscure topic",
    )
    await execute_content_task(
        task.id,
        sessionmaker=sessionmaker,
        tavily_client=FakeTavilyClient({"results": []}),
    )

    stored = await fetch_task(sessionmaker, task.id)
    logs = await fetch_tool_logs(sessionmaker)

    assert stored.status == TaskStatus.SUCCESS.value
    assert stored.result_text is not None
    assert "没有找到可用搜索结果" in stored.result_text
    assert "来源" not in stored.result_text
    assert "http" not in stored.result_text
    search_log = next(log for log in logs if log.tool_name == "search.web")
    assert search_log.status == "succeeded"


@pytest.mark.asyncio
async def test_08_phase_boundaries_avoid_out_of_scope_services(
    monkeypatch: pytest.MonkeyPatch,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    model_calls: list[str] = []

    async def fail_if_model_provider_is_called(*_args: Any, **_kwargs: Any) -> None:
        model_calls.append("called")
        raise AssertionError("content commands must not call model providers directly")

    monkeypatch.setattr(DeepSeekAdapter, "chat", fail_if_model_provider_is_called)

    for task_type in ("learn", "daily", "office"):
        _user, task = await create_user_and_task(
            sessionmaker,
            task_type=task_type,
            input_text=f"/{task_type} boundary check",
        )
        await execute_content_task(
            task.id,
            sessionmaker=sessionmaker,
            tavily_client=FakeTavilyClient(tavily_sources_response()),
        )

    async with sessionmaker() as session:
        memory_count = len((await session.scalars(select(Memory))).all())

    assert model_calls == []
    assert memory_count == 0


def test_09_readme_documents_search_content_command_phase() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "MVP 阶段 09" in readme
    assert "TAVILY_BASE_URL" in readme
    assert "TAVILY_API_KEY" in readme
    assert "`/learn` 通过 `search.web`" in readme
    assert "`/daily` 通过 `search.web`" in readme
    assert "`/office` 默认不执行搜索" in readme
    assert "`search.web`" in readme
    assert "完整 MCP Gateway" in readme
    assert "深度浏览" in readme
    assert "真实 Office 文件生成" in readme
    assert "邮件/日历接入" in readme
