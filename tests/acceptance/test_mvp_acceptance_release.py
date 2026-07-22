from __future__ import annotations

from collections.abc import AsyncIterator
import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from infrastructure.settings.config import Settings
from domain.models import (
    Base,
    ProcessedMessage,
    Task,
    TaskStatus,
    ToolLog,
    User,
)
from workers.runtime import execute_task_by_id
from agent import (
    AgentRunInput,
    LangGraphExecutionResult,
)
from model_gateway.deepseek import DeepSeekAdapter
from tools import TavilySearchRequest


TAVILY_API_KEY = "fake-tavily-api-key"
LANGBOT_API_KEY = "fake-langbot-api-key"
SECRET_TOKEN = "secret-token-value"
PRIVATE_URL = "https://private.example.invalid/langbot"


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/mvp-acceptance-release.db",
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


def release_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///unused.db",
        redis_url="redis://redis:6379/0",
        langbot_webhook_secret="placeholder-langbot-webhook-secret",
        langbot_api_base_url="https://langbot.invalid",
        langbot_api_key=LANGBOT_API_KEY,
        tavily_base_url="https://tavily.invalid",
        tavily_api_key=TAVILY_API_KEY,
        tavily_max_results=5,
        deepseek_api_key="fake-deepseek-key-that-release-worker-must-not-read",
    )


async def create_user(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> User:
    async with sessionmaker() as session:
        user = User(display_name="Release User")
        session.add(user)
        await session.commit()
        return user


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    task_type: str,
    input_text: str,
    status: TaskStatus = TaskStatus.PENDING,
) -> Task:
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="langbot",
            task_type=task_type,
            input_text=input_text,
            status=status.value,
        )
        session.add(task)
        await session.commit()
        return task


async def bind_dispatch_target(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    task_id: str,
    message_id: str,
    conversation_id: str,
) -> None:
    async with sessionmaker() as session:
        session.add(
            ProcessedMessage(
                platform="langbot",
                message_id=message_id,
                reason="task_created",
                task_id=task_id,
                chat_id=conversation_id,
                response_target=json.dumps(
                    {
                        "adapter": "discord",
                        "conversation_id": conversation_id,
                        "conversation_type": "group",
                    }
                ),
            )
        )
        await session.commit()


async def create_successful_dispatch_log(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    task_id: str,
    conversation_id: str,
) -> None:
    async with sessionmaker() as session:
        session.add(
            ToolLog(
                task_id=task_id,
                tool_name="langbot.result_dispatch",
                status="succeeded",
                input_text=f'{{"conversation_id":"{conversation_id}"}}',
                output_text='{"message_id":"lb_already_sent"}',
            )
        )
        await session.commit()


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
    assert LANGBOT_API_KEY not in value
    assert SECRET_TOKEN not in value
    assert PRIVATE_URL not in value
    assert "Bearer " not in value
    assert "authorization" not in value.lower()
    assert "cookie" not in value.lower()
    assert "traceback" not in value.lower()


class FakeTavilyClient:
    def __init__(self) -> None:
        self.calls: list[TavilySearchRequest] = []

    async def search(self, request: TavilySearchRequest) -> dict[str, Any]:
        self.calls.append(request)
        return {
            "results": [
                {
                    "title": f"Source for {request.query}",
                    "url": f"https://example.com/{len(self.calls)}",
                    "content": "release source summary",
                    "score": 0.91,
                }
            ]
        }


class FakeLangBotClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def send_message(
        self,
        *,
        adapter: str,
        conversation_id: str,
        conversation_type: str,
        text: str,
        **_kwargs: str,
    ) -> dict[str, str]:
        self.calls.append(
            {
                "adapter": adapter,
                "conversation_id": conversation_id,
                "conversation_type": conversation_type,
                "text": text,
            }
        )
        return {"message_id": f"lb_sent_{len(self.calls)}"}


class FakeLangGraphExecutor:
    def __init__(
        self,
        *,
        result: LangGraphExecutionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def execute(self, *, run_input: AgentRunInput) -> LangGraphExecutionResult:
        plan = run_input.plan
        context = run_input.context
        self.calls.append(
            {
                "task_id": context.task_id,
                "task_type": context.task_type,
                "goal": plan.goal,
                "allowed_tools": tuple(plan.allowed_tools),
            }
        )
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return LangGraphExecutionResult(
            result_text="langgraph release result",
            tool_calls=tuple(plan.allowed_tools),
            loop_steps=max(len(plan.steps), 1),
            checkpoint_id=f"ckpt-{context.task_id[:8]}",
        )


def test_01_docker_compose_release_contracts_are_placeholder_safe() -> None:
    dockerfile = Path("Dockerfile")
    compose = Path("docker-compose.yml")
    env_example = Path(".env.example")

    assert dockerfile.exists()
    assert compose.exists()
    assert env_example.exists()

    dockerfile_text = dockerfile.read_text(encoding="utf-8")
    compose_text = compose.read_text(encoding="utf-8")
    env_text = env_example.read_text(encoding="utf-8")

    assert "python:3.12" in dockerfile_text
    assert "uv sync" in dockerfile_text
    for service_name in ["assistant-api", "postgres", "redis", "celery-worker"]:
        assert f"  {service_name}:" in compose_text
    for out_of_scope_service in [
        "deepseek:",
        "tavily:",
        "model-gateway:",
    ]:
        assert f"  {out_of_scope_service}" not in compose_text

    for key in [
        "DATABASE_URL",
        "REDIS_URL",
        "LANGBOT_WEBHOOK_SECRET",
        "LANGBOT_API_BASE_URL",
        "LANGBOT_API_KEY",
        "TAVILY_API_KEY",
        "DEEPSEEK_API_KEY",
        "RUNNING_TASK_TIMEOUT_SECONDS",
        "PENDING_TASK_COMPENSATION_DELAY_SECONDS",
    ]:
        assert f"{key}=" in env_text
    assert "placeholder" in env_text
    assert "sk-" not in env_text
    assert "Bearer " not in env_text
    assert "secret-token-value" not in env_text


@pytest.mark.asyncio
async def test_02_worker_executes_pending_task_and_records_safe_failure(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await create_user(sessionmaker)
    success_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="plan",
        input_text="/plan release success",
    )
    failure_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="plan",
        input_text="/plan release failure",
    )

    await execute_task_by_id(
        success_task.id,
        sessionmaker=sessionmaker,
        settings=release_settings(),
        langgraph_executor=FakeLangGraphExecutor(
            result=LangGraphExecutionResult(
                result_text="langgraph release result",
                tool_calls=("search.web",),
                loop_steps=1,
                checkpoint_id="ckpt-release-success",
            )
        ),
        tavily_client=FakeTavilyClient(),
        langbot_client=FakeLangBotClient(),
    )
    unsafe_error = RuntimeError(
        "Traceback Authorization: Bearer "
        f"{TAVILY_API_KEY} cookie={SECRET_TOKEN} {PRIVATE_URL}"
    )
    await execute_task_by_id(
        failure_task.id,
        sessionmaker=sessionmaker,
        settings=release_settings(),
        langgraph_executor=FakeLangGraphExecutor(error=unsafe_error),
        tavily_client=FakeTavilyClient(),
        langbot_client=FakeLangBotClient(),
    )

    stored_success = await fetch_task(sessionmaker, success_task.id)
    stored_failure = await fetch_task(sessionmaker, failure_task.id)

    assert stored_success.status == TaskStatus.SUCCESS.value
    assert stored_success.workflow_key == "langgraph.plan"
    assert stored_success.result_text == "langgraph release result"
    assert stored_failure.status == TaskStatus.FAILED.value
    assert_no_sensitive_text(stored_failure.error_message)


@pytest.mark.asyncio
async def test_03_release_worker_covers_all_mvp_commands_with_mocked_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    model_calls: list[str] = []

    async def fail_if_model_provider_is_called(*_args: Any, **_kwargs: Any) -> None:
        model_calls.append("called")
        raise AssertionError("release worker must not call model providers directly")

    monkeypatch.setattr(DeepSeekAdapter, "chat", fail_if_model_provider_is_called)

    user = await create_user(sessionmaker)
    command_inputs = [
        ("plan", "/plan 做 MVP 验收"),
        ("learn", "/learn Python Agent"),
        ("daily", "/daily AI workflow"),
        ("office", "/office 整理会议纪要"),
        ("memory", "/memory 记住 输出先给结论"),
        ("status", "/status"),
    ]
    tasks: list[Task] = []
    for index, (task_type, input_text) in enumerate(command_inputs, start=1):
        task = await create_task(
            sessionmaker,
            user_id=user.id,
            task_type=task_type,
            input_text=input_text,
        )
        await bind_dispatch_target(
            sessionmaker,
            task_id=task.id,
            message_id=f"lb_release_{index}",
            conversation_id=f"conv_release_{index}",
        )
        tasks.append(task)

    langgraph_executor = FakeLangGraphExecutor()
    tavily_client = FakeTavilyClient()
    langbot_client = FakeLangBotClient()
    for task in tasks:
        await execute_task_by_id(
            task.id,
            sessionmaker=sessionmaker,
            settings=release_settings(),
            langgraph_executor=langgraph_executor,
            tavily_client=tavily_client,
            langbot_client=langbot_client,
        )

    stored_tasks = [await fetch_task(sessionmaker, task.id) for task in tasks]

    assert [task.status for task in stored_tasks] == [TaskStatus.SUCCESS.value] * 6
    assert [task.workflow_key for task in stored_tasks[:4]] == [
        "langgraph.plan",
        "langgraph.learn",
        "langgraph.daily",
        "langgraph.office",
    ]
    assert [call["task_type"] for call in langgraph_executor.calls] == [
        "plan",
        "learn",
        "daily",
        "office",
    ]
    assert tavily_client.calls == []
    assert len(langbot_client.calls) == 6
    assert model_calls == []
    assert "已保存记忆" in (stored_tasks[4].result_text or "")
    assert "任务状态" in (stored_tasks[5].result_text or "")


@pytest.mark.asyncio
async def test_04_worker_invokes_dispatcher_and_preserves_duplicate_prevention(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await create_user(sessionmaker)
    first_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="plan",
        input_text="/plan should dispatch",
    )
    duplicate_task = await create_task(
        sessionmaker,
        user_id=user.id,
        task_type="plan",
        input_text="/plan already dispatched",
    )
    await bind_dispatch_target(
        sessionmaker,
        task_id=first_task.id,
        message_id="lb_dispatch_first",
        conversation_id="conv_dispatch_first",
    )
    await bind_dispatch_target(
        sessionmaker,
        task_id=duplicate_task.id,
        message_id="lb_dispatch_duplicate",
        conversation_id="conv_dispatch_duplicate",
    )
    await create_successful_dispatch_log(
        sessionmaker,
        task_id=duplicate_task.id,
        conversation_id="conv_dispatch_duplicate",
    )

    langbot_client = FakeLangBotClient()
    langgraph_executor = FakeLangGraphExecutor()
    for task in [first_task, duplicate_task]:
        await execute_task_by_id(
            task.id,
            sessionmaker=sessionmaker,
            settings=release_settings(),
            langgraph_executor=langgraph_executor,
            tavily_client=FakeTavilyClient(),
            langbot_client=langbot_client,
        )

    assert [call["conversation_id"] for call in langbot_client.calls] == [
        "conv_dispatch_first"
    ]
    logs = await fetch_tool_logs(sessionmaker)
    successful_dispatch_logs = [
        log
        for log in logs
        if log.tool_name == "langbot.result_dispatch" and log.status == "succeeded"
    ]
    assert len(successful_dispatch_logs) == 2


def test_05_readme_documents_phase09_state() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "MVP 阶段 09" in readme
    assert "MVP 阶段 08 Acceptance Release" not in readme
    assert "docker compose up --build" in readme
    assert "celery-worker" in readme
    assert "workers.worker:celery_app" in readme
    assert "POST /api/webhooks/langbot" in readme
    assert "LangBot" in readme
    assert "waiting_approval" in readme
    assert "超时 `running` 任务失败" in readme
    assert "`pending` 任务补偿" in readme
    assert "uv run pytest" in readme
    assert "uv run ruff check ." in readme
    assert "uv run mypy ." in readme
    assert "真实 LangBot" in readme
    assert "LangGraph" in readme
    assert "Dify" not in readme
