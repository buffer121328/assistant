from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from application.lifecycle_hooks import (
    HookDefinition,
    HookMatcher,
    HookRegistrationError,
    LifecycleHookRegistry,
    build_lifecycle_event,
)
from domain.models import Base, LifecycleHookExecution


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/lifecycle-hooks.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def event(**payload: object):
    return build_lifecycle_event(
        event_type="task.completed",
        actor_user_id="user-1",
        tenant_id="tenant-1",
        task_id="task-1",
        payload=payload,
    )


@pytest.mark.asyncio
async def test_event_is_bounded_and_redacts_sensitive_context() -> None:
    item = event(
        result_preview="done",
        token="secret-value",
        authorization="Bearer secret-value",
        raw_context="private model transcript",
        nested={"cookie": "cookie-value", "safe": "kept"},
    )

    assert item.idempotency_key
    assert item.event_id
    assert item.payload == {
        "nested": {"safe": "kept"},
        "result_preview": "done",
    }


def test_registry_rejects_untrusted_executable_hook() -> None:
    registry = LifecycleHookRegistry()

    async def handler(_event):
        return None

    with pytest.raises(HookRegistrationError):
        registry.register(
            HookDefinition(
                name="unsafe.hook",
                version="1",
                source_kind="builtin",
                source_id="tests",
                matcher=HookMatcher(frozenset({"task.completed"})),
                mode="observer",
                handler=handler,
                shell_command="rm -rf /",
            )
        )


@pytest.mark.asyncio
async def test_observers_run_by_priority_and_failure_is_isolated() -> None:
    registry = LifecycleHookRegistry()
    calls: list[str] = []

    async def high(_event):
        calls.append("high")
        return None

    async def low(_event):
        calls.append("low")
        raise RuntimeError("not exposed")

    registry.register(
        HookDefinition(
            name="audit.high",
            version="1",
            source_kind="builtin",
            source_id="tests",
            matcher=HookMatcher(frozenset({"task.completed"})),
            mode="observer",
            handler=high,
            priority=20,
        )
    )
    registry.register(
        HookDefinition(
            name="metrics.low",
            version="1",
            source_kind="builtin",
            source_id="tests",
            matcher=HookMatcher(frozenset({"task.completed"})),
            mode="observer",
            handler=low,
            priority=10,
        )
    )

    result = await registry.dispatch_observers(event(result_preview="done"))

    assert calls == ["high", "low"]
    assert [item.result for item in result] == ["succeeded", "failed"]
    assert result[1].reason == "Hook execution failed"


@pytest.mark.asyncio
async def test_observer_timeout_does_not_change_core_result() -> None:
    registry = LifecycleHookRegistry()

    async def slow(_event):
        import asyncio

        await asyncio.sleep(0.05)
        return None

    registry.register(
        HookDefinition(
            name="notify.slow",
            version="1",
            source_kind="builtin",
            source_id="tests",
            matcher=HookMatcher(frozenset({"task.completed"})),
            mode="observer",
            handler=slow,
            timeout_seconds=0.001,
        )
    )

    result = await registry.dispatch_observers(event(result_preview="done"))

    assert result[0].result == "timeout"
    assert result[0].failure_class == "timeout"


@pytest.mark.asyncio
async def test_guards_aggregate_deny_over_approval_and_allow() -> None:
    registry = LifecycleHookRegistry()

    async def allow(_event):
        return "allow"

    async def approval(_event):
        return "require_approval"

    async def deny(_event):
        return "deny"

    for name, priority, handler in (
        ("guard.allow", 30, allow),
        ("guard.approval", 20, approval),
        ("guard.deny", 10, deny),
    ):
        registry.register(
            HookDefinition(
                name=name,
                version="1",
                source_kind="builtin",
                source_id="tests",
                matcher=HookMatcher(frozenset({"before.external.write"})),
                mode="guard",
                handler=handler,
                priority=priority,
            )
        )

    before_event = build_lifecycle_event(
        event_type="before.external.write",
        actor_user_id="user-1",
        tenant_id="tenant-1",
        task_id="task-1",
        payload={"tool": "send_email"},
    )
    result = await registry.dispatch_guards(before_event)

    assert result.decision == "deny"
    assert [item.result for item in result.executions] == [
        "allow",
        "require_approval",
        "deny",
    ]


@pytest.mark.asyncio
async def test_guard_failure_fails_closed_or_requires_approval() -> None:
    registry = LifecycleHookRegistry()

    async def fail(_event):
        raise RuntimeError("secret failure")

    registry.register(
        HookDefinition(
            name="guard.closed",
            version="1",
            source_kind="builtin",
            source_id="tests",
            matcher=HookMatcher(frozenset({"before.external.write"})),
            mode="guard",
            handler=fail,
            fail_closed=True,
        )
    )
    registry.register(
        HookDefinition(
            name="guard.review",
            version="1",
            source_kind="builtin",
            source_id="tests",
            matcher=HookMatcher(frozenset({"before.external.write"})),
            mode="guard",
            handler=fail,
            fail_closed=False,
        )
    )

    before_event = build_lifecycle_event(
        event_type="before.external.write",
        actor_user_id="user-1",
        tenant_id="tenant-1",
        task_id="task-1",
    )
    result = await registry.dispatch_guards(before_event)

    assert result.decision == "deny"
    assert all(item.reason == "Hook execution failed" for item in result.executions)


@pytest.mark.asyncio
async def test_persisted_successful_observer_is_not_repeated(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    registry = LifecycleHookRegistry(sessionmaker=sessionmaker)
    calls = 0

    async def notify(_event):
        nonlocal calls
        calls += 1
        return None

    registry.register(
        HookDefinition(
            name="notify.once",
            version="1",
            source_kind="builtin",
            source_id="tests",
            matcher=HookMatcher(frozenset({"task.completed"})),
            mode="observer",
            handler=notify,
        )
    )

    first = await registry.dispatch_observers(event(result_preview="done"))
    second = await registry.dispatch_observers(event(result_preview="done"))

    assert calls == 1
    assert first[0].result == "succeeded"
    assert second[0].result == "skipped"
    assert second[0].skipped is True
    async with sessionmaker() as session:
        rows = list(await session.scalars(select(LifecycleHookExecution)))
    assert len(rows) == 1
    assert rows[0].reason == ""
    assert rows[0].hook_source == "builtin:tests"


@pytest.mark.asyncio
async def test_tool_registry_hands_off_lifecycle_guard_before_handler(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from tools.core.registry import (
        ToolInvocation,
        ToolNotAllowedError,
        ToolRegistry,
        ToolSpec,
    )
    from domain.models import Task, TaskStatus, User

    async with sessionmaker() as session:
        user = User(display_name="Hook user")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type="plan",
            input_text="guard this action",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        lifecycle = LifecycleHookRegistry()
        calls = 0
        guard_payloads: list[dict[str, object]] = []

        async def deny(item):
            guard_payloads.append(dict(item.payload))
            return "deny"

        async def handler(_invocation):
            nonlocal calls
            calls += 1
            return "sent"

        lifecycle.register(
            HookDefinition(
                name="guard.test-deny",
                version="1",
                source_kind="builtin",
                source_id="tests",
                matcher=HookMatcher(frozenset({"before.tool"})),
                mode="guard",
                handler=deny,
            )
        )
        tools = ToolRegistry(session=session, lifecycle_registry=lifecycle)
        tools.register(
            ToolSpec(
                name="email.send",
                description="send",
                risk_level="R3",
                handler=handler,
            )
        )

        with pytest.raises(ToolNotAllowedError):
            await tools.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=user.id,
                    name="email.send",
                    arguments={"to": ["person@example.invalid"]},
                ),
                allowed_tools=("email.send",),
                approval_required_tools=(),
            )

        assert calls == 0
        assert guard_payloads[0]["arguments_hash"]
        assert "person@example.invalid" not in repr(guard_payloads[0])
        assert not isinstance(guard_payloads[0]["arguments_hash"], dict)


@pytest.mark.asyncio
async def test_lifecycle_producers_publish_approval_and_run_facts(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from application.task_execution.lifecycle import ApprovalService, TaskService
    from domain.models import Approval, ApprovalStatus, Task, TaskStatus, User
    from workers.runtime import _publish_run_lifecycle_event

    observed = []
    registry = LifecycleHookRegistry()

    async def observer(item):
        observed.append(item)
        return None

    registry.register(
        HookDefinition(
            name="test.lifecycle-producers",
            version="1",
            source_kind="builtin",
            source_id="tests",
            matcher=HookMatcher(frozenset({"approval.resolved", "run.completed"})),
            mode="observer",
            handler=observer,
        )
    )

    async with sessionmaker() as session:
        user = User(display_name="Lifecycle producer user")
        session.add(user)
        await session.flush()
        approval_task = Task(
            user_id=user.id,
            platform="api",
            task_type="plan",
            input_text="approve action",
            status=TaskStatus.RUNNING.value,
        )
        run_task = Task(
            user_id=user.id,
            platform="api",
            task_type="plan",
            input_text="completed run",
            status=TaskStatus.SUCCESS.value,
        )
        session.add_all([approval_task, run_task])
        await session.commit()

        await TaskService(session).save_waiting_approval(
            approval_task.id,
            "Approval required",
            requested_tools=("email.send",),
        )
        approval = await session.scalar(
            select(Approval).where(Approval.task_id == approval_task.id)
        )
        assert approval is not None
        approval_result = await ApprovalService(
            session, lifecycle_registry=registry
        ).decide(
            task_id=approval_task.id,
            approval_id=approval.id,
            user_id=user.id,
            decision=ApprovalStatus.APPROVED,
        )
        await _publish_run_lifecycle_event(
            lifecycle_registry=registry,
            task=run_task,
            agent_run_id="run-lifecycle-producer",
        )

    assert approval_result.changed is True
    assert [item.event_type for item in observed] == [
        "approval.resolved",
        "run.completed",
    ]
    assert observed[0].payload["approval_id"] == approval.id
    assert observed[0].payload["decision"] == ApprovalStatus.APPROVED.value
    assert observed[1].run_id == "run-lifecycle-producer"


@pytest.mark.asyncio
async def test_harness_keeps_explicit_lifecycle_and_fallback_callbacks_observable(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from application.runtime_dependencies import SqlAlchemyTaskLifecyclePort
    from domain.models import Task, TaskStatus, User
    from runtime.runner_harness import AgentHarness
    from runtime.runner_types import ExecutionOutcome

    class Boundary:
        async def execute(self, **kwargs):
            return ExecutionOutcome(
                status=TaskStatus.SUCCESS.value,
                result_text="completed",
                workflow_key=kwargs["plan"].workflow_key,
            )

    async with sessionmaker() as session:
        user = User(display_name="Harness lifecycle user")
        session.add(user)
        await session.flush()
        fallback_task = Task(
            user_id=user.id,
            platform="api",
            task_type="plan",
            input_text="fallback callback",
            status=TaskStatus.PENDING.value,
        )
        explicit_task = Task(
            user_id=user.id,
            platform="api",
            task_type="plan",
            input_text="explicit lifecycle",
            status=TaskStatus.PENDING.value,
        )
        session.add_all([fallback_task, explicit_task])
        await session.commit()

        fallback_callbacks: list[str] = []
        explicit_callbacks: list[str] = []
        legacy_completed_events: list[str] = []
        observed: list[str] = []
        registry = LifecycleHookRegistry()

        async def observer(item):
            observed.append(item.task_id or "")
            return None

        async def fallback_callback(task):
            fallback_callbacks.append(task.id)

        async def explicit_callback(task):
            explicit_callbacks.append(task.id)

        async def event_sink(event_type: str, payload: dict[str, object]) -> None:
            if event_type == "task.completed":
                legacy_completed_events.append(str(payload["status"]))

        registry.register(
            HookDefinition(
                name="test.task-observer",
                version="1",
                source_kind="builtin",
                source_id="tests",
                matcher=HookMatcher(frozenset({"task.completed"})),
                mode="observer",
                handler=observer,
            )
        )

        fallback_result = await AgentHarness(
            session=session,
            executor=Boundary(),
            lifecycle_registry=registry,
            memory_candidate_hook=fallback_callback,
            event_sink=event_sink,
        ).execute_task(fallback_task.id)
        explicit_result = await AgentHarness(
            session=session,
            executor=Boundary(),
            lifecycle_registry=registry,
            memory_candidate_hook=fallback_callback,
            event_sink=event_sink,
            task_lifecycle=SqlAlchemyTaskLifecyclePort(
                session, success_hook=explicit_callback
            ),
        ).execute_task(explicit_task.id)

    assert fallback_result.status == TaskStatus.SUCCESS.value
    assert explicit_result.status == TaskStatus.SUCCESS.value
    assert fallback_callbacks == [fallback_task.id]
    assert explicit_callbacks == [explicit_task.id]
    assert legacy_completed_events == [
        TaskStatus.SUCCESS.value,
        TaskStatus.SUCCESS.value,
    ]
    assert observed == [fallback_task.id, explicit_task.id]


@pytest.mark.asyncio
async def test_persisted_guard_uses_the_active_sqlite_transaction(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Hook execution facts must not open a second SQLite writer during a guard."""
    async with sessionmaker() as session:
        registry = LifecycleHookRegistry(sessionmaker=sessionmaker, session=session)

        async def allow(_event):
            return "allow"

        registry.register(
            HookDefinition(
                name="guard.current-transaction",
                version="1",
                source_kind="builtin",
                source_id="tests",
                matcher=HookMatcher(frozenset({"before.external.write"})),
                mode="guard",
                handler=allow,
            )
        )
        before_event = build_lifecycle_event(
            event_type="before.external.write",
            actor_user_id="user-1",
            tenant_id="tenant-1",
            task_id="task-1",
            payload={"tool_name": "email.send"},
        )

        result = await registry.dispatch_guards(before_event)
        rows = list(await session.scalars(select(LifecycleHookExecution)))

        assert result.decision == "allow"
        assert [(row.hook_name, row.result) for row in rows] == [
            ("guard.current-transaction", "allow")
        ]
        await session.commit()
