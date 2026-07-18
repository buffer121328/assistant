from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domain.models import Base, Task, User
from agent.modeling.agent_model import AgentDecisionError, parse_agent_decision
from agent.core.subagents import (
    SubAgentCoordinator,
    SubAgentRequest,
    SubAgentResult,
)
from agent.tool_management import ToolInvocation, ToolNotAllowedError, ToolRegistry, ToolSpec


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
