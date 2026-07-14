from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from assistant_api.models import Base, Memory, Task, TaskStatus, ToolLog, User
from packages.agent_harness import (
    AgentHarness,
    AgentRunInput,
    AgentProfile,
    DefaultPlanningLayer,
    ExecutionBoundary,
    ExecutionPlan,
    LangGraphExecutionResult,
    TaskContext,
    UnsupportedWorkflowTaskTypeError,
)


SECRET_TOKEN = "secret-token-value"
PRIVATE_URL = "https://private.example.invalid/phase09"


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/langgraph-harness-refactor.db",
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


async def create_user_and_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    task_type: str = "plan",
    status: TaskStatus = TaskStatus.PENDING,
    input_text: str = "/plan 产出 phase 09 改造计划",
    model_class: str | None = None,
) -> tuple[User, Task]:
    async with sessionmaker() as session:
        user = User(display_name=f"Phase09 User {task_type}")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type=task_type,
            input_text=input_text,
            status=status.value,
            model_class=model_class,
        )
        session.add(task)
        await session.commit()
        return user, task


async def create_memory(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    content: str,
) -> Memory:
    async with sessionmaker() as session:
        memory = Memory(user_id=user_id, content=content)
        session.add(memory)
        await session.commit()
        return memory


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
    assert SECRET_TOKEN not in value
    assert PRIVATE_URL not in value
    assert "Bearer " not in value
    assert "authorization" not in value.lower()
    assert "cookie" not in value.lower()


class FakeLangGraphExecutor:
    def __init__(
        self,
        result: LangGraphExecutionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or LangGraphExecutionResult(
            result_text="LangGraph 主路径结果",
            tool_calls=("memory.read",),
            loop_steps=2,
            checkpoint_id="ckpt-phase09",
        )
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def execute(self, *, run_input: AgentRunInput) -> LangGraphExecutionResult:
        self.calls.append(
            {"plan": run_input.plan, "context": run_input.context}
        )
        if self.error is not None:
            raise self.error
        return self.result


class FixedPlanningLayer:
    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan
        self.calls: list[dict[str, Any]] = []

    def build_plan(
        self,
        *,
        task: Task,
        profile: AgentProfile,
        context: TaskContext,
    ) -> ExecutionPlan:
        self.calls.append(
            {
                "task_id": task.id,
                "task_type": task.task_type,
                "profile": profile,
                "context": context,
            }
        )
        return self.plan


@pytest.mark.asyncio
async def test_01_planning_layer_builds_phase09_execution_plan(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_user_and_task(
        sessionmaker,
        task_type="learn",
        input_text="/learn LangGraph checkpoint 是什么",
    )
    await create_memory(
        sessionmaker,
        user_id=user.id,
        content="输出先给结论",
    )

    planner = DefaultPlanningLayer()
    profile = AgentProfile(
        name="v2.researcher",
        executor_kind="langgraph",
        workflow_key="langgraph.learn",
    )
    context = TaskContext(
        task_id=task.id,
        user_id=user.id,
        task_type=task.task_type,
        input_text=task.input_text,
        memory_summary="输出先给结论",
        allowed_tools=("search.web",),
    )

    plan = planner.build_plan(
        task=task,
        profile=profile,
        context=context,
    )

    assert plan.goal == "LangGraph checkpoint 是什么"
    assert plan.steps
    assert "search.web" in plan.allowed_tools
    assert plan.approval_required_tools == ()
    assert plan.max_steps >= 3
    assert plan.timeout_seconds > 0
    assert plan.risk_level in {"low", "medium", "high"}
    assert plan.output_format == "markdown"


@pytest.mark.asyncio
async def test_02_agent_harness_executes_plan_task_through_primary_langgraph_boundary(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_user_and_task(sessionmaker)
    fake_langgraph = FakeLangGraphExecutor()

    async with sessionmaker() as session:
        boundary = ExecutionBoundary(
            session=session,
            langgraph_executor=fake_langgraph,
        )
        result = await AgentHarness(
            session=session,
            executor=boundary,
        ).execute_task(task.id)

    stored = await fetch_task(sessionmaker, task.id)
    logs = await fetch_tool_logs(sessionmaker)

    assert result.status == TaskStatus.SUCCESS.value
    assert stored.status == TaskStatus.SUCCESS.value
    assert stored.workflow_key == "langgraph.plan"
    assert stored.result_text == "LangGraph 主路径结果"
    assert len(fake_langgraph.calls) == 1
    assert fake_langgraph.calls[0]["context"].user_id == user.id
    assert fake_langgraph.calls[0]["context"].memory_summary == ""
    assert logs[0].tool_name == "langgraph.executor"
    assert "checkpoint_id" in (logs[0].output_text or "")


@pytest.mark.asyncio
async def test_04_memory_and_status_stay_on_local_service_path(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, memory_task = await create_user_and_task(
        sessionmaker,
        task_type="memory",
        input_text="/memory 记住 phase 09 先补测试",
    )
    _same_user, _done_task = await create_user_and_task(
        sessionmaker,
        task_type="plan",
        status=TaskStatus.SUCCESS,
        input_text="/plan 已完成任务",
    )
    _same_user, status_task = await create_user_and_task(
        sessionmaker,
        task_type="status",
        input_text="/status",
    )
    status_task.user_id = user.id
    fake_langgraph = FakeLangGraphExecutor()

    async with sessionmaker() as session:
        stored_status_task = await session.get(Task, status_task.id)
        assert stored_status_task is not None
        stored_status_task.user_id = user.id
        await session.commit()

    async with sessionmaker() as session:
        boundary = ExecutionBoundary(
            session=session,
            langgraph_executor=fake_langgraph,
        )
        harness = AgentHarness(
            session=session,
            executor=boundary,
        )
        memory_result = await harness.execute_task(memory_task.id)
        status_result = await harness.execute_task(status_task.id)

    stored_memory = await fetch_task(sessionmaker, memory_task.id)
    stored_status = await fetch_task(sessionmaker, status_task.id)
    assert memory_result.status == TaskStatus.SUCCESS.value
    assert status_result.status == TaskStatus.SUCCESS.value
    assert "已保存记忆" in (stored_memory.result_text or "")
    assert "任务状态" in (stored_status.result_text or "")
    assert fake_langgraph.calls == []


@pytest.mark.asyncio
async def test_05_execution_boundary_rejects_unauthorized_tool_request_safely(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _user, task = await create_user_and_task(sessionmaker)
    fake_langgraph = FakeLangGraphExecutor(
        LangGraphExecutionResult(
            result_text="尝试直接执行 shell",
            requested_tools=("shell.exec",),
        )
    )
    planner = FixedPlanningLayer(
        ExecutionPlan(
            goal="执行 shell",
            steps=("检查命令",),
            allowed_tools=(),
            approval_required_tools=(),
            max_steps=1,
            timeout_seconds=30.0,
            risk_level="high",
            output_format="markdown",
            profile_name="phase09.plan",
            executor_kind="langgraph",
            workflow_key="langgraph.plan",
        )
    )

    async with sessionmaker() as session:
        boundary = ExecutionBoundary(
            session=session,
            langgraph_executor=fake_langgraph,
            sensitive_values=[SECRET_TOKEN, PRIVATE_URL],
        )
        result = await AgentHarness(
            session=session,
            executor=boundary,
            planning_layer=planner,
        ).execute_task(task.id)

    stored = await fetch_task(sessionmaker, task.id)
    assert result.status == TaskStatus.FAILED.value
    assert stored.status == TaskStatus.FAILED.value
    assert_no_sensitive_text(stored.error_message)
    assert "未授权" in (stored.error_message or "")


@pytest.mark.asyncio
async def test_06_agent_harness_persists_waiting_approval_without_auto_running_gated_tool(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _user, task = await create_user_and_task(sessionmaker)
    fake_langgraph = FakeLangGraphExecutor(
        LangGraphExecutionResult(
            result_text="需要执行 shell.exec",
            requested_tools=("shell.exec",),
        )
    )
    planner = FixedPlanningLayer(
        ExecutionPlan(
            goal="执行 shell",
            steps=("请求审批",),
            allowed_tools=(),
            approval_required_tools=("shell.exec",),
            max_steps=1,
            timeout_seconds=30.0,
            risk_level="high",
            output_format="markdown",
            profile_name="phase09.plan",
            executor_kind="langgraph",
            workflow_key="langgraph.plan",
        )
    )

    async with sessionmaker() as session:
        boundary = ExecutionBoundary(
            session=session,
            langgraph_executor=fake_langgraph,
        )
        result = await AgentHarness(
            session=session,
            executor=boundary,
            planning_layer=planner,
        ).execute_task(task.id)

    stored = await fetch_task(sessionmaker, task.id)
    assert result.status == TaskStatus.WAITING_APPROVAL.value
    assert stored.status == TaskStatus.WAITING_APPROVAL.value
    assert "审批" in ((stored.result_text or "") + (stored.error_message or ""))


@pytest.mark.asyncio
async def test_07_unsupported_task_type_is_rejected_without_executor_call(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _user, task = await create_user_and_task(
        sessionmaker,
        task_type="router",
        input_text="/router 不在 phase 09",
    )
    fake_langgraph = FakeLangGraphExecutor()

    async with sessionmaker() as session:
        boundary = ExecutionBoundary(
            session=session,
            langgraph_executor=fake_langgraph,
        )
        with pytest.raises(UnsupportedWorkflowTaskTypeError):
            await AgentHarness(
                session=session,
                executor=boundary,
            ).execute_task(task.id)

    stored = await fetch_task(sessionmaker, task.id)
    assert stored.status == TaskStatus.PENDING.value
    assert fake_langgraph.calls == []
