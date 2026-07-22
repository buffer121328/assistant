from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domain.models import Base, Task, User
from agent import AgentDecision
from agent.modeling.agent_model import AgentDecisionError, parse_agent_decision
from runtime.subagents import (
    SubAgentCoordinator,
    SubAgentRequest,
    SubAgentResult,
)
from runtime import subagent_gateway as subagent_gateway_module
from tools import ToolInvocation, ToolNotAllowedError, ToolRegistry, ToolSpec


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v4-parallel.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def test_agent_decision_accepts_only_bounded_unique_tool_batch() -> None:
    decision = parse_agent_decision(
        '{"action":"tool_batch","tool_calls":['
        '{"id":"a","tool_name":"office.create_docx","arguments":{"filename":"a.docx"}},'
        '{"id":"b","tool_name":"office.create_xlsx","arguments":{"filename":"b.xlsx"}}]}'
    )

    assert decision.action == "tool_batch"
    assert [item.call_id for item in decision.tool_calls] == ["a", "b"]

    with pytest.raises(AgentDecisionError):
        parse_agent_decision(
            '{"action":"tool_batch","tool_calls":['
            '{"id":"same","tool_name":"a.tool","arguments":{}},'
            '{"id":"same","tool_name":"b.tool","arguments":{}}]}'
        )


@pytest.mark.asyncio
async def test_tool_batch_preauthorizes_all_before_parallel_execution(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    executed: list[str] = []

    async def handler(invocation: ToolInvocation) -> dict[str, str]:
        await asyncio.sleep(0)
        executed.append(invocation.name)
        return {"name": invocation.name}

    async with sessionmaker() as session:
        user = User(display_name="parallel")
        session.add(user)
        await session.flush()
        task = Task(user_id=user.id, platform="api", task_type="office", input_text="x", status="running")
        session.add(task)
        await session.flush()
        registry = ToolRegistry(session=session, snapshot_revision=1)
        registry.register(ToolSpec(name="office.a", description="a", risk_level="L1", handler=handler, version="1", parallel_safe=True))
        registry.register(ToolSpec(name="office.unsafe", description="unsafe", risk_level="L3", handler=handler, version="1", parallel_safe=True))

        invocations = (
            ToolInvocation(task_id=task.id, user_id=user.id, name="office.a", tool_snapshot_revision=1, tool_version="1"),
            ToolInvocation(task_id=task.id, user_id=user.id, name="office.unsafe", tool_snapshot_revision=1, tool_version="1"),
        )
        with pytest.raises(ToolNotAllowedError):
            await registry.execute_batch(invocations, allowed_tools=("office.a", "office.unsafe"), approval_required_tools=())

        assert executed == []


@pytest.mark.asyncio
async def test_subagent_coordinator_bounds_fanout_and_preserves_order() -> None:
    active = 0
    peak = 0

    class Runner:
        async def run(self, request: SubAgentRequest) -> SubAgentResult:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return SubAgentResult(step_index=request.step_index, role=request.role, content=f"done:{request.objective}")

    coordinator = SubAgentCoordinator(runner=Runner(), max_subagents=3, concurrency=2, timeout_seconds=1)
    results = await coordinator.run(
        task_id="task-1",
        user_id="user-1",
        requests=tuple(
            SubAgentRequest(step_index=index, role="researcher", objective=f"step-{index}", context="bounded")
            for index in range(5)
        ),
    )

    assert [item.step_index for item in results] == [0, 1, 2]
    assert peak == 2


@pytest.mark.asyncio
async def test_gateway_subagent_runner_commits_final_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(commits=0, rollbacks=0)
    captured: dict[str, object] = {}

    class FakeSessionContext:
        async def __aenter__(self) -> SimpleNamespace:
            return session

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

    class FakeSessionmaker:
        def __call__(self) -> FakeSessionContext:
            return FakeSessionContext()

    class FakeModel:
        def __init__(self, *, session, settings, observability) -> None:
            captured["session"] = session
            captured["settings"] = settings
            captured["observability"] = observability

        async def decide(self, request):
            captured["request"] = request
            return AgentDecision(action="final", answer="subagent-result")

    async def commit() -> None:
        session.commits += 1

    async def rollback() -> None:
        session.rollbacks += 1

    session.commit = commit  # type: ignore[assignment]
    session.rollback = rollback  # type: ignore[assignment]

    monkeypatch.setattr(subagent_gateway_module, "AgentGatewayModel", FakeModel)

    runner = subagent_gateway_module.GatewaySubAgentRunner(
        sessionmaker=cast(Any, FakeSessionmaker()),
        settings=cast(Any, SimpleNamespace()),
        observability=SimpleNamespace(),
    )
    request = SubAgentRequest(
        step_index=7,
        role="researcher",
        objective="请归纳这段内容的关键结论",
        context="上下文" * 6000,
        task_id="task-1",
        user_id="user-1",
    )

    result = await runner.run(request)

    assert result == SubAgentResult(
        step_index=7,
        role="researcher",
        content="subagent-result",
    )
    assert session.commits == 1
    assert session.rollbacks == 0
    assert captured["session"] is session
    request_payload = cast(Any, captured["request"])
    prompt = request_payload.messages[0].content
    assert "你是受限子 Agent" in prompt
    assert "角色：researcher" in prompt
    assert request.objective[:1000] in prompt
    assert request.context[:20000] in prompt


@pytest.mark.asyncio
async def test_gateway_subagent_runner_rejects_non_final_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(commits=0, rollbacks=0)

    class FakeSessionContext:
        async def __aenter__(self) -> SimpleNamespace:
            return session

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

    class FakeSessionmaker:
        def __call__(self) -> FakeSessionContext:
            return FakeSessionContext()

    class FakeModel:
        def __init__(self, *, session, settings, observability) -> None:
            del session, settings, observability

        async def decide(self, request):
            del request
            return AgentDecision(action="tool_call", tool_name="search.web", arguments={})

    async def commit() -> None:
        session.commits += 1

    async def rollback() -> None:
        session.rollbacks += 1

    session.commit = commit  # type: ignore[assignment]
    session.rollback = rollback  # type: ignore[assignment]

    monkeypatch.setattr(subagent_gateway_module, "AgentGatewayModel", FakeModel)

    runner = subagent_gateway_module.GatewaySubAgentRunner(
        sessionmaker=cast(Any, FakeSessionmaker()),
        settings=cast(Any, SimpleNamespace()),
        observability=SimpleNamespace(),
    )

    with pytest.raises(RuntimeError, match="Subagent attempted a non-final action"):
        await runner.run(
            SubAgentRequest(
                step_index=0,
                role="researcher",
                objective="检查子任务安全性",
                context="上下文",
                task_id="task-2",
                user_id="user-1",
            )
        )

    assert session.commits == 0
    assert session.rollbacks == 1
