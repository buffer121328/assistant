from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import json
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from runtime.budget import BudgetExceededError, RunBudget
from runtime.loop import ControlledLoop, LoopStepLimitError
from tools import ToolInvocation, ToolRegistry, ToolSpec
from tools.core.registry import ToolHandler
from domain.models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    Base,
    Task,
    TaskEvent,
    TaskStatus,
    ToolLog,
    User,
)
from model_gateway import (
    GatewayMessage,
    GatewayRequest,
    GatewayResult,
    GatewayUsage,
    ModelGatewayError,
    ModelNode,
    PooledModelGateway,
)
from workers.monitoring import (
    diagnose_waiting_approval_tasks,
    fail_timed_out_running_tasks,
)


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v12-budget-recovery.db",
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    status: TaskStatus = TaskStatus.PENDING,
) -> Task:
    async with sessionmaker() as session:
        user = User(display_name="V12 budget user")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type="plan",
            input_text="/plan test v12 budgets",
            status=status.value,
        )
        session.add(task)
        await session.commit()
        return task


def request(max_tokens: int = 10) -> GatewayRequest:
    return GatewayRequest(
        user_id="user-1",
        task_id="task-1",
        task_type="plan",
        model_class=None,
        messages=(GatewayMessage(role="user", content="hello"),),
        temperature=0.0,
        max_tokens=max_tokens,
    )


async def fetch_logs(
    sessionmaker: async_sessionmaker[AsyncSession], task_id: str
) -> list[ToolLog]:
    async with sessionmaker() as session:
        rows = await session.scalars(
            select(ToolLog)
            .where(ToolLog.task_id == task_id)
            .order_by(ToolLog.created_at, ToolLog.id)
        )
        return list(rows)


async def fetch_events(
    sessionmaker: async_sessionmaker[AsyncSession], task_id: str
) -> list[TaskEvent]:
    async with sessionmaker() as session:
        rows = await session.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.sequence)
        )
        return list(rows)


@pytest.mark.asyncio
async def test_run_budget_stops_step_tool_token_and_deadline_before_side_effects(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, status=TaskStatus.RUNNING)
    now = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)

    async with sessionmaker() as session:
        step_budget = RunBudget(max_steps=0, deadline_at=now + timedelta(seconds=60))
        loop = ControlledLoop(
            session=session,
            task_id=task.id,
            max_steps=10,
            budget=step_budget,
            now=lambda: now,
        )
        side_effects: list[str] = []
        with pytest.raises(LoopStepLimitError):
            await loop.run_step("model", lambda: _side_effect(side_effects, "step"))
        assert step_budget.stop_reason == "step_limit_exceeded"
        assert side_effects == []
        await session.commit()

    async with sessionmaker() as session:
        tool_budget = RunBudget(max_tool_calls=0, deadline_at=now + timedelta(seconds=60))
        registry = ToolRegistry(session=session)

        async def handler(_invocation: ToolInvocation) -> dict[str, bool]:
            side_effects.append("tool")
            return {"ok": True}

        registry.register(
            ToolSpec(
                name="budget.tool",
                description="Budgeted tool",
                risk_level="L1",
                handler=cast(ToolHandler, handler),
            )
        )
        with pytest.raises(BudgetExceededError) as tool_error:
            await registry.execute(
                ToolInvocation(task_id=task.id, user_id=task.user_id, name="budget.tool"),
                allowed_tools=("budget.tool",),
                approval_required_tools=(),
                budget=tool_budget,
            )
        assert tool_error.value.stop_reason == "tool_call_limit_exceeded"
        assert side_effects == []
        await session.commit()

    token_budget = RunBudget(
        max_input_tokens=10,
        max_output_tokens=5,
        deadline_at=now + timedelta(seconds=60),
    )
    with pytest.raises(BudgetExceededError) as token_error:
        token_budget.record_model_usage(input_tokens=11, output_tokens=1)
    assert token_error.value.stop_reason == "token_limit_exceeded"
    assert token_budget.summary()["stop_reason"] == "token_limit_exceeded"

    deadline_budget = RunBudget(deadline_at=now - timedelta(seconds=1))
    with pytest.raises(BudgetExceededError) as deadline_error:
        deadline_budget.check_deadline(now=now)
    assert deadline_error.value.stop_reason == "deadline_exceeded"

    logs = await fetch_logs(sessionmaker, task.id)
    serialized = "\n".join(filter(None, [log.error_message or log.output_text for log in logs]))
    assert "step_limit_exceeded" in serialized
    assert "tool_call_limit_exceeded" in serialized


@pytest.mark.asyncio
async def test_recovery_marks_stale_running_and_waiting_approval_idempotently(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    stale = await create_task(sessionmaker, status=TaskStatus.RUNNING)
    waiting = await create_task(sessionmaker, status=TaskStatus.WAITING_APPROVAL)
    now = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)

    async with sessionmaker() as session:
        stale_task = await session.get(Task, stale.id)
        waiting_task = await session.get(Task, waiting.id)
        assert stale_task is not None and waiting_task is not None
        stale_task.updated_at = now - timedelta(seconds=600)
        waiting_task.updated_at = now - timedelta(seconds=600)
        session.add(
            Approval(
                task_id=waiting.id,
                status=ApprovalStatus.PENDING.value,
                tool_name="external.write",
                approval_type=ApprovalType.TOOL.value,
                subject="external.write",
                request_summary="Need approval",
            )
        )
        await session.commit()

    async with sessionmaker() as session:
        timed_out = await fail_timed_out_running_tasks(
            session=session,
            timeout_seconds=300.0,
            now=now,
        )
    async with sessionmaker() as session:
        first = await diagnose_waiting_approval_tasks(session=session, now=now)
    async with sessionmaker() as session:
        second = await diagnose_waiting_approval_tasks(session=session, now=now)

    stale_events = await fetch_events(sessionmaker, stale.id)
    waiting_events = await fetch_events(sessionmaker, waiting.id)
    async with sessionmaker() as session:
        approval_count = await session.scalar(select(func.count()).select_from(Approval))
        stored_stale = await session.get(Task, stale.id)
        stored_waiting = await session.get(Task, waiting.id)

    assert timed_out == [stale.id]
    assert first == [waiting.id]
    assert second == []
    assert approval_count == 1
    assert stored_stale is not None and stored_stale.status == TaskStatus.FAILED.value
    assert stored_waiting is not None and stored_waiting.status == TaskStatus.WAITING_APPROVAL.value
    assert [event.event_type for event in stale_events] == ["task.recovery.dead_letter"]
    assert [event.event_type for event in waiting_events] == ["task.recovery.waiting_approval"]
    assert json.loads(stale_events[0].payload_json)["recovery_status"] == "dead_letter"
    assert json.loads(waiting_events[0].payload_json)["recovery_status"] == "waiting_approval"


@pytest.mark.asyncio
async def test_high_risk_non_idempotent_retry_requires_idempotency_key(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, status=TaskStatus.RUNNING)
    calls: list[str] = []

    async def handler(_invocation: ToolInvocation) -> dict[str, bool]:
        calls.append("executed")
        return {"ok": True}

    async with sessionmaker() as session:
        session.add(
            ToolLog(
                task_id=task.id,
                tool_name="external.write",
                status="succeeded",
                input_text='{"arguments":{}}',
                output_text='{"sent":true}',
            )
        )
        session.add(
            Approval(
                task_id=task.id,
                status=ApprovalStatus.APPROVED.value,
                tool_name="external.write",
                approval_type=ApprovalType.TOOL.value,
                subject="external.write",
            )
        )
        registry = ToolRegistry(session=session)
        registry.register(
            ToolSpec(
                name="external.write",
                description="Non-idempotent external write",
                risk_level="L3",
                handler=cast(ToolHandler, handler),
                idempotent=False,
                input_schema={"type": "object"},
            )
        )
        with pytest.raises(ValueError, match="idempotency"):
            await registry.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=task.user_id,
                    name="external.write",
                    arguments={"target": "a@example.invalid"},
                ),
                allowed_tools=("external.write",),
                approval_required_tools=(),
            )
        await session.commit()

    logs = await fetch_logs(sessionmaker, task.id)
    assert calls == []
    assert logs[-1].status == "failed"
    assert "idempotency" in (logs[-1].error_message or "")


class FakeAdapter:
    def __init__(
        self,
        *,
        fail: Exception | None = None,
        usage: GatewayUsage = GatewayUsage(3, 2),
    ) -> None:
        self.fail = fail
        self.usage = usage
        self.calls = 0

    async def chat(self, request: GatewayRequest, node: ModelNode) -> GatewayResult:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return GatewayResult(
            node.provider,
            node.model,
            "ok",
            self.usage,
            7,
        )

    async def stream_chat(self, request: GatewayRequest, node: ModelNode):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        yield "ok"


@pytest.mark.asyncio
async def test_models_cooldown_fallback_rate_limit_and_cost_diagnostics() -> None:
    now_value = 1000.0

    def now() -> float:
        return now_value

    primary = ModelNode(
        "fast-a",
        "fast",
        "deepseek",
        "https://a.invalid/v1",
        "a",
        "key",
        2,
        0.5,
        rpm_limit=10,
        tpm_limit=100,
        input_token_cost=0.001,
        output_token_cost=0.002,
    )
    fallback = ModelNode(
        "fast-b",
        "fast",
        "glm",
        "https://b.invalid/v1",
        "b",
        "key",
        2,
        0.5,
        rpm_limit=10,
        tpm_limit=100,
        input_token_cost=0.01,
        output_token_cost=0.02,
    )
    events: list[dict[str, object]] = []
    adapters = {
        "fast-a": FakeAdapter(
            fail=ModelGatewayError("provider_429", "rate limited", 429)
        ),
        "fast-b": FakeAdapter(usage=GatewayUsage(3, 2)),
    }
    gateway = PooledModelGateway(
        (primary, fallback),
        adapters=adapters,
        failure_threshold=1,
        cooldown_seconds=60.0,
        now=now,
        diagnostic_sink=events.append,
    )

    first = await gateway.chat(request(max_tokens=5), "fast")
    second = await gateway.chat(request(max_tokens=5), "fast")

    assert first.model == "b"
    assert second.model == "b"
    assert adapters["fast-a"].calls == 1
    assert adapters["fast-b"].calls == 2
    assert gateway.balancer.metrics("fast-a").health_status == "cooldown"
    assert events[0]["event_type"] == "model_gateway.fallback"
    assert events[0]["from_node"] == "fast-a"
    assert events[0]["to_node"] == "fast-b"
    assert first.estimated_cost == pytest.approx(0.07)

    limited = ModelNode(
        "fast-limited",
        "fast",
        "deepseek",
        "https://limited.invalid/v1",
        "limited",
        "key",
        1,
        0.5,
        rpm_limit=0,
    )
    limited_adapter = FakeAdapter()
    limited_gateway = PooledModelGateway(
        (limited,),
        adapters={"fast-limited": limited_adapter},
        now=now,
    )
    with pytest.raises(ModelGatewayError):
        await limited_gateway.chat(request(), "fast")
    assert limited_adapter.calls == 0


async def _side_effect(target: list[str], value: str) -> str:
    target.append(value)
    return value


def test_desktop_renders_budget_and_recovery_diagnostics(tmp_path) -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtCore")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from assistant_desktop.window import TaskWindow

    _app = QApplication.instance() or QApplication([])
    window = TaskWindow(
        settings=QSettings(str(tmp_path / "diagnostics.ini"), QSettings.Format.IniFormat)
    )
    window._task_event_received(
        {
            "type": "task.budget.stopped",
            "payload": {
                "stop_reason": "tool_call_limit_exceeded",
                "budget": {"used": {"steps": 3, "tool_calls": 2, "input_tokens": 10, "output_tokens": 5}},
            },
        }
    )
    window._task_event_received(
        {
            "type": "task.recovery.dead_letter",
            "payload": {
                "recovery_status": "dead_letter",
                "retryable": False,
                "reason": "running_timeout",
            },
        }
    )

    text = window.task_result.toPlainText()
    assert "tool_call_limit_exceeded" in text
    assert "tool_calls=2" in text
    assert "dead_letter" in text
    assert "不可自动重试" in text
    window.shutdown()
