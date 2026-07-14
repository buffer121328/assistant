from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from assistant_api.config import Settings
from assistant_api.models import Base, ModelLog, Task, TaskStatus, User
from assistant_api.worker_runtime import execute_task_by_id
from packages.agent_harness import AgentRunInput, AgentRunResult
from packages.agent_harness.routing import (
    InvalidAgentRouteDecisionError,
    build_agent_route_candidates,
    parse_agent_route_decision,
)
from packages.capabilities import (
    CapabilityKind,
    CapabilityMetadata,
    CapabilityRegistry,
)
from packages.model_gateway import GatewayResult, GatewayUsage


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v3-routing.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def routing_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///unused.db",
        deepseek_api_key="placeholder-routing-key",
        deepseek_base_url="https://deepseek.invalid/v1",
        deepseek_light_model="deepseek-light-test",
        deepseek_standard_model="deepseek-standard-test",
        tavily_base_url="https://tavily.invalid",
        tavily_api_key="placeholder-tavily-key",
    )


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    task_type: str,
    input_text: str = "帮我规划一个轻量私人助理",
) -> Task:
    async with sessionmaker() as session:
        user = User(display_name="V3 Routing User")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="desktop",
            task_type=task_type,
            input_text=input_text,
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.commit()
        return task


async def fetch_model_logs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> list[ModelLog]:
    async with sessionmaker() as session:
        return list(
            await session.scalars(select(ModelLog).order_by(ModelLog.created_at))
        )


class FakeRoutingAdapter:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[tuple[Any, str]] = []

    async def chat(self, request: Any, model_class: str) -> GatewayResult:
        self.calls.append((request, model_class))
        if self.error is not None:
            raise self.error
        assert self.content is not None
        return GatewayResult(
            provider="deepseek",
            model="deepseek-light-test",
            content=self.content,
            usage=GatewayUsage(input_tokens=20, output_tokens=8),
            latency_ms=3,
        )


class RecordingAgentExecutor:
    def __init__(self) -> None:
        self.inputs: list[AgentRunInput] = []

    async def execute(self, *, run_input: AgentRunInput) -> AgentRunResult:
        self.inputs.append(run_input)
        return AgentRunResult(
            result_text=f"executed {run_input.context.task_type}",
            loop_steps=1,
        )


def metadata(
    capability_id: str,
    kind: CapabilityKind,
    *,
    enabled: bool = True,
) -> CapabilityMetadata:
    return CapabilityMetadata(
        id=capability_id,
        kind=kind,
        display_name=capability_id,
        summary=f"Summary for {capability_id}",
        source="test",
        enabled=enabled,
        risk_level="L1",
        requires_approval=False,
    )


def test_router_candidates_are_registry_constrained_and_metadata_only() -> None:
    registry = CapabilityRegistry()
    registry.register(metadata("profile.plan", CapabilityKind.AGENT_PROFILE))
    registry.register(
        metadata("profile.learn", CapabilityKind.AGENT_PROFILE, enabled=False)
    )
    registry.register(metadata("profile.extra", CapabilityKind.AGENT_PROFILE))
    registry.register(metadata("skill.research", CapabilityKind.SKILL))
    registry.register(metadata("tool.search-web", CapabilityKind.TOOL))

    candidates = build_agent_route_candidates(registry)

    assert [(item.capability_id, item.task_type) for item in candidates] == [
        ("profile.plan", "plan")
    ]
    assert candidates[0].display_name == "profile.plan"
    assert not hasattr(candidates[0], "loader")
    assert not hasattr(candidates[0], "path")


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        '```json\n{"capability_id":"profile.plan","confidence":1,"reason":"ok"}\n```',
        '{"capability_id":"profile.plan","confidence":1,"reason":"ok","extra":1}',
        '{"capability_id":"profile.plan","confidence":1.1,"reason":"ok"}',
        '{"capability_id":"profile.unknown","confidence":1,"reason":"ok"}',
        '{"capability_id":"tool.search.web","confidence":1,"reason":"ok"}',
    ],
)
def test_router_rejects_malformed_or_unapproved_model_decision(content: str) -> None:
    registry = CapabilityRegistry()
    registry.register(metadata("profile.plan", CapabilityKind.AGENT_PROFILE))
    candidates = build_agent_route_candidates(registry)

    with pytest.raises(InvalidAgentRouteDecisionError):
        parse_agent_route_decision(content, candidates)


def test_router_accepts_one_strict_known_profile_decision() -> None:
    registry = CapabilityRegistry()
    registry.register(metadata("profile.learn", CapabilityKind.AGENT_PROFILE))
    candidates = build_agent_route_candidates(registry)

    decision = parse_agent_route_decision(
        '{"capability_id":"profile.learn","confidence":0.92,"reason":"需要检索并学习"}',
        candidates,
    )

    assert decision.capability_id == "profile.learn"
    assert decision.task_type == "learn"
    assert decision.confidence == 0.92


@pytest.mark.asyncio
async def test_worker_routes_agent_once_then_uses_existing_profile_and_audits(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, task_type="agent")
    adapter = FakeRoutingAdapter(
        '{"capability_id":"profile.plan","confidence":0.95,"reason":"规划请求"}'
    )
    executor = RecordingAgentExecutor()

    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=routing_settings(),
        routing_adapter=adapter,
        langgraph_executor=executor,
    )
    logs = await fetch_model_logs(sessionmaker)

    assert result.status == TaskStatus.SUCCESS.value
    assert result.task_type == "plan"
    assert result.workflow_key == "langgraph.plan"
    assert [item.context.task_type for item in executor.inputs] == ["plan"]
    assert len(adapter.calls) == 1
    request, model_class = adapter.calls[0]
    assert request.task_type == "router"
    assert model_class == "light"
    prompt = "\n".join(message.content for message in request.messages)
    assert all(
        profile_id in prompt
        for profile_id in (
            "profile.plan",
            "profile.learn",
            "profile.daily",
            "profile.office",
        )
    )
    assert "tool.search.web" not in prompt
    assert "skill." not in prompt
    assert len(logs) == 1
    assert logs[0].error_message is None
    assert "placeholder-routing-key" not in (logs[0].request_text or "")


@pytest.mark.asyncio
async def test_worker_bypasses_router_for_fixed_task(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, task_type="office")
    adapter = FakeRoutingAdapter(error=AssertionError("router must not run"))
    executor = RecordingAgentExecutor()

    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=routing_settings(),
        routing_adapter=adapter,
        langgraph_executor=executor,
    )

    assert result.status == TaskStatus.SUCCESS.value
    assert [item.context.task_type for item in executor.inputs] == ["office"]
    assert adapter.calls == []
    assert await fetch_model_logs(sessionmaker) == []


@pytest.mark.asyncio
async def test_invalid_route_fails_safely_without_agent_execution(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, task_type="agent")
    secret = routing_settings().deepseek_api_key
    adapter = FakeRoutingAdapter(
        f'{{"capability_id":"tool.search.web","confidence":1,"reason":"Bearer {secret}"}}'
    )
    executor = RecordingAgentExecutor()

    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=routing_settings(),
        routing_adapter=adapter,
        langgraph_executor=executor,
    )
    logs = await fetch_model_logs(sessionmaker)

    assert result.status == TaskStatus.FAILED.value
    assert result.task_type == "agent"
    assert executor.inputs == []
    assert result.error_message == "Invalid Agent route decision"
    assert len(logs) == 1
    audit_text = "\n".join(
        value
        for value in (
            logs[0].request_text,
            logs[0].response_text,
            logs[0].error_message,
            result.error_message,
        )
        if value
    )
    assert secret not in audit_text
    assert "Bearer " not in audit_text
    assert "traceback" not in audit_text.lower()
