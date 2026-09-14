from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent.governance.content import ContentGuardRequest, LocalContentGuard
from app.main import create_app
from application.task_execution.events import TaskEventPublisher
from domain.models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    Base,
    Task,
    TaskStatus,
    ToolLog,
    User,
)
from domain.policies.tool_approval import external_approval_binding
from infrastructure.settings.config import Settings
from runtime.budget import BudgetExceededError, RunBudget
from runtime.governance_events import EventSinkGovernanceDecisionRecorder
from tools import (
    ToolApprovalRequiredError,
    ToolArgumentsInvalidError,
    ToolInvocation,
    ToolNotAllowedError,
    ToolRegistry,
    ToolSnapshotStaleError,
    ToolSpec,
)
from tools.core.registry import ToolHandler


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v12-tool-hardening.db",
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_task(sessionmaker: async_sessionmaker[AsyncSession]) -> Task:
    async with sessionmaker() as session:
        user = User(display_name="V12 tool hardening user")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type="plan",
            input_text="validate governed tool invocation",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.commit()
        return task


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


def object_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1}},
        "required": ["query"],
        "additionalProperties": False,
    }


def test_v12_scope_and_readme_expose_local_only_quality_boundary() -> None:
    index = (REPOSITORY_ROOT / "docs/v12/index.md").read_text(encoding="utf-8")
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert "本地单用户" in index
    assert "不推进 CI/CD" in index
    assert "docs/v12/index.md" in readme


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": 3},
        {"query": "safe", "unexpected": "private-token"},
    ],
)
@pytest.mark.asyncio
async def test_registry_rejects_invalid_arguments_before_handler_and_audits_safely(
    sessionmaker: async_sessionmaker[AsyncSession],
    arguments: dict[str, Any],
) -> None:
    task = await create_task(sessionmaker)
    calls: list[ToolInvocation] = []

    async def handler(invocation: ToolInvocation) -> dict[str, bool]:
        calls.append(invocation)
        return {"executed": True}

    async with sessionmaker() as session:
        registry = ToolRegistry(session=session, sensitive_values=("private-token",))
        registry.register(
            ToolSpec(
                name="search.strict",
                description="Strict schema test tool",
                risk_level="L1",
                handler=cast(ToolHandler, handler),
                input_schema=object_schema(),
            )
        )

        with pytest.raises(ToolArgumentsInvalidError, match="search.strict"):
            await registry.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=task.user_id,
                    name="search.strict",
                    arguments=arguments,
                ),
                allowed_tools=("search.strict",),
                approval_required_tools=(),
            )
        await session.commit()

    [log] = await fetch_logs(sessionmaker, task.id)
    assert calls == []
    assert log.status == "failed"
    assert log.error_message is not None
    assert "private-token" not in log.error_message
    assert "private-token" not in (log.input_text or "")


@pytest.mark.parametrize("risk_level", ["L3", "L4"])
@pytest.mark.asyncio
async def test_high_risk_tool_spec_requires_approval_even_when_plan_omits_gate(
    sessionmaker: async_sessionmaker[AsyncSession], risk_level: str
) -> None:
    task = await create_task(sessionmaker)
    calls = 0

    async def handler(_invocation: ToolInvocation) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        return {"executed": True}

    async with sessionmaker() as session:
        registry = ToolRegistry(session=session)
        registry.register(
            ToolSpec(
                name="external.write",
                description="High risk write",
                risk_level=risk_level,  # type: ignore[arg-type]
                handler=cast(ToolHandler, handler),
                input_schema={"type": "object", "additionalProperties": False},
            )
        )

        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=task.user_id,
                    name="external.write",
                ),
                allowed_tools=("external.write",),
                approval_required_tools=(),
            )
        await session.commit()

    [log] = await fetch_logs(sessionmaker, task.id)
    assert calls == 0
    assert log.status == "waiting_approval"


@pytest.mark.asyncio
async def test_registry_rejects_stale_snapshot_and_version_before_handler(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    calls = 0

    async def handler(_invocation: ToolInvocation) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        return {"executed": True}

    async with sessionmaker() as session:
        registry = ToolRegistry(session=session, snapshot_revision=7)
        registry.register(
            ToolSpec(
                name="versioned.read",
                description="Versioned tool",
                risk_level="L1",
                handler=cast(ToolHandler, handler),
                version="2",
            )
        )
        for invocation in (
            ToolInvocation(
                task_id=task.id,
                user_id=task.user_id,
                name="versioned.read",
                tool_snapshot_revision=6,
                tool_version="2",
            ),
            ToolInvocation(
                task_id=task.id,
                user_id=task.user_id,
                name="versioned.read",
                tool_snapshot_revision=7,
                tool_version="1",
            ),
        ):
            with pytest.raises(ToolSnapshotStaleError):
                await registry.execute(
                    invocation,
                    allowed_tools=("versioned.read",),
                    approval_required_tools=(),
                )
        await session.commit()

    logs = await fetch_logs(sessionmaker, task.id)
    assert calls == 0
    assert [log.status for log in logs] == ["failed", "failed"]


@pytest.mark.asyncio
async def test_batch_schema_failure_schedules_no_handlers(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    calls: list[str] = []

    async def handler(invocation: ToolInvocation) -> dict[str, str]:
        calls.append(invocation.arguments["query"])
        return {"query": invocation.arguments["query"]}

    async with sessionmaker() as session:
        registry = ToolRegistry(session=session)
        registry.register(
            ToolSpec(
                name="parallel.search",
                description="Parallel strict search",
                risk_level="L1",
                handler=cast(ToolHandler, handler),
                input_schema=object_schema(),
                parallel_safe=True,
            )
        )

        with pytest.raises(ToolArgumentsInvalidError):
            await registry.execute_batch(
                (
                    ToolInvocation(
                        task_id=task.id,
                        user_id=task.user_id,
                        name="parallel.search",
                        arguments={"query": "valid"},
                    ),
                    ToolInvocation(
                        task_id=task.id,
                        user_id=task.user_id,
                        name="parallel.search",
                        arguments={"query": 9},
                    ),
                ),
                allowed_tools=("parallel.search",),
                approval_required_tools=(),
            )
        await session.commit()

    logs = await fetch_logs(sessionmaker, task.id)
    assert calls == []
    assert len(logs) == 1
    assert logs[0].status == "failed"


def test_tool_spec_governance_defaults_and_invalid_schema_registration() -> None:
    async def handler(_invocation: ToolInvocation) -> dict[str, bool]:
        return {"ok": True}

    spec = ToolSpec(
        name="metadata.read",
        description="Metadata defaults",
        risk_level="L1",
        handler=cast(ToolHandler, handler),
    )

    assert spec.requires_approval is False
    assert spec.timeout_seconds > 0
    assert spec.max_retries == 0
    assert spec.idempotent is False
    assert spec.supports_dry_run is False
    assert spec.compensation_tool is None
    assert spec.required_permissions == ()


@pytest.mark.asyncio
async def test_registry_rejects_invalid_schema_and_bounds_sanitized_output(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    secret = "private-token"

    async def handler(_invocation: ToolInvocation) -> dict[str, str]:
        return {"content": f"{secret}-" + ("x" * 10_000)}

    async with sessionmaker() as session:
        registry = ToolRegistry(session=session, sensitive_values=(secret,))
        with pytest.raises(ValueError, match="schema"):
            registry.register(
                ToolSpec(
                    name="invalid.schema",
                    description="Invalid schema",
                    risk_level="L1",
                    handler=cast(ToolHandler, handler),
                    input_schema={"type": "not-a-json-schema-type"},
                )
            )

        registry.register(
            ToolSpec(
                name="bounded.output",
                description="Large output",
                risk_level="L1",
                handler=cast(ToolHandler, handler),
            )
        )
        result = await registry.execute(
            ToolInvocation(
                task_id=task.id,
                user_id=task.user_id,
                name="bounded.output",
            ),
            allowed_tools=("bounded.output",),
            approval_required_tools=(),
        )
        await session.commit()

    [log] = await fetch_logs(sessionmaker, task.id)
    assert secret in result["content"]
    assert log.output_text is not None
    assert secret not in log.output_text
    assert "[REDACTED]" in log.output_text
    assert len(log.output_text) <= 4_000


@pytest.mark.asyncio
async def test_governance_decision_denies_unplanned_tool_after_content_allow_and_projects_safely(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    secret = "raw-governance-secret"
    handler_calls = 0
    buffered_events: list[tuple[str, dict[str, object]]] = []

    async def buffer_event(event_type: str, payload: dict[str, object]) -> None:
        buffered_events.append((event_type, dict(payload)))

    guard_decision = await LocalContentGuard().inspect_final_result(
        ContentGuardRequest.for_final_result(
            task_id=task.id,
            user_id=task.user_id,
            task_type="daily",
            text="内容边界允许此文本。",
            safe_source_urls=(),
        )
    )
    assert guard_decision.outcome == "allow"

    async def handler(_invocation: ToolInvocation) -> dict[str, bool]:
        nonlocal handler_calls
        handler_calls += 1
        return {"executed": True}

    async with sessionmaker() as session:
        registry = ToolRegistry(
            session=session,
            sensitive_values=(secret,),
            decision_recorder=EventSinkGovernanceDecisionRecorder(buffer_event),
        )
        registry.register(
            ToolSpec(
                name="search.unplanned",
                description="An execution-plan denial test tool",
                risk_level="L1",
                handler=cast(ToolHandler, handler),
                input_schema=object_schema(),
            )
        )

        with pytest.raises(ToolNotAllowedError, match="execution plan"):
            await registry.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=task.user_id,
                    name="search.unplanned",
                    arguments={"query": secret},
                ),
                allowed_tools=(),
                approval_required_tools=(),
            )
        await session.commit()

    assert handler_calls == 0
    assert buffered_events == [
        (
            "governance.decision",
            {
                "outcome": "deny",
                "reason_code": "tool_not_in_plan",
                "tool_name": "search.unplanned",
            },
        )
    ]
    assert secret not in str(buffered_events)

    publisher = TaskEventPublisher(sessionmaker)
    for event_type, payload in buffered_events:
        await publisher.publish(
            task_id=task.id,
            user_id=task.user_id,
            event_type=event_type,
            payload=payload,
        )

    app = create_app(Settings(database_url="sqlite+aiosqlite:///unused.db"))
    app.state.db_sessionmaker = sessionmaker
    with TestClient(app) as client:
        response = client.get(
            f"/api/tasks/{task.id}/diagnostics",
            params={"user_id": task.user_id},
        )

    assert response.status_code == 200
    decision_events = [
        item
        for item in response.json()["events"]
        if item["type"] == "governance.decision"
    ]
    assert len(decision_events) == 1
    assert decision_events[0]["payload"] == buffered_events[0][1]
    assert secret not in str(decision_events[0])


@pytest.mark.asyncio
async def test_governance_decisions_require_exact_owned_approval_before_allowing_handler(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    calls: list[ToolInvocation] = []
    buffered_events: list[tuple[str, dict[str, object]]] = []
    arguments = {
        "url": "https://example.com/approved-action",
        "actions": ["click"],
    }

    async def buffer_event(event_type: str, payload: dict[str, object]) -> None:
        buffered_events.append((event_type, dict(payload)))

    async def handler(invocation: ToolInvocation) -> dict[str, bool]:
        calls.append(invocation)
        return {"executed": True}

    async with sessionmaker() as session:
        other_user = User(display_name="Different approval owner")
        session.add(other_user)
        await session.flush()
        registry = ToolRegistry(
            session=session,
            decision_recorder=EventSinkGovernanceDecisionRecorder(buffer_event),
        )
        registry.register(
            ToolSpec(
                name="browser.interact",
                description="Exact approval binding test tool",
                risk_level="L3",
                handler=cast(ToolHandler, handler),
                idempotent=True,
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "actions": {"type": "array"},
                    },
                    "required": ["url", "actions"],
                    "additionalProperties": False,
                },
            )
        )
        matching = ToolInvocation(
            task_id=task.id,
            user_id=task.user_id,
            name="browser.interact",
            arguments=arguments,
        )

        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                matching,
                allowed_tools=("browser.interact",),
                approval_required_tools=(),
            )

        binding = external_approval_binding("browser.interact", arguments)
        session.add(
            Approval(
                task_id=task.id,
                tool_name="browser.interact",
                approval_type=ApprovalType.TOOL.value,
                subject=binding.subject,
                status=ApprovalStatus.APPROVED.value,
                decided_by_user_id=task.user_id,
            )
        )
        await session.flush()

        assert await registry.execute(
            matching,
            allowed_tools=("browser.interact",),
            approval_required_tools=(),
        ) == {"executed": True}

        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=other_user.id,
                    name="browser.interact",
                    arguments=arguments,
                ),
                allowed_tools=("browser.interact",),
                approval_required_tools=(),
            )
        await session.commit()

    assert calls == [matching]
    decisions = [payload for _event_type, payload in buffered_events]
    assert [payload["outcome"] for payload in decisions] == [
        "require_approval",
        "allow",
        "require_approval",
    ]
    assert [payload["reason_code"] for payload in decisions] == [
        "tool_approval_required",
        "tool_authorized",
        "tool_approval_required",
    ]
    assert decisions[0]["approval_fingerprint"] == binding.fingerprint
    assert decisions[1]["approval_fingerprint"] == binding.fingerprint
    assert "url" not in decisions[0]
    assert "actions" not in decisions[0]


@pytest.mark.asyncio
async def test_batch_governance_decisions_cover_allowed_validation_and_budget_outcomes(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    calls: list[str] = []
    buffered_events: list[tuple[str, dict[str, object]]] = []

    async def buffer_event(event_type: str, payload: dict[str, object]) -> None:
        buffered_events.append((event_type, dict(payload)))

    async def handler(invocation: ToolInvocation) -> dict[str, str]:
        calls.append(str(invocation.arguments["query"]))
        return {"query": str(invocation.arguments["query"])}

    async with sessionmaker() as session:
        registry = ToolRegistry(
            session=session,
            decision_recorder=EventSinkGovernanceDecisionRecorder(buffer_event),
        )
        registry.register(
            ToolSpec(
                name="parallel.governed",
                description="Parallel governance test tool",
                risk_level="L1",
                handler=cast(ToolHandler, handler),
                input_schema=object_schema(),
                parallel_safe=True,
            )
        )
        invocations = (
            ToolInvocation(
                task_id=task.id,
                user_id=task.user_id,
                name="parallel.governed",
                arguments={"query": "one"},
            ),
            ToolInvocation(
                task_id=task.id,
                user_id=task.user_id,
                name="parallel.governed",
                arguments={"query": "two"},
            ),
        )

        assert await registry.execute_batch(
            invocations,
            allowed_tools=("parallel.governed",),
            approval_required_tools=(),
        ) == ({"query": "one"}, {"query": "two"})
        assert [payload["outcome"] for _, payload in buffered_events] == [
            "allow",
            "allow",
        ]
        assert calls == ["one", "two"]

        buffered_events.clear()
        calls.clear()
        with pytest.raises(BudgetExceededError):
            await registry.execute_batch(
                invocations,
                allowed_tools=("parallel.governed",),
                approval_required_tools=(),
                budget=RunBudget(max_tool_calls=1),
            )
        await session.commit()

    assert calls == []
    assert [payload["outcome"] for _, payload in buffered_events] == ["deny", "deny"]
    assert [payload["reason_code"] for _, payload in buffered_events] == [
        "run_budget_exhausted",
        "run_budget_exhausted",
    ]
