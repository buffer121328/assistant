from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent.tool_management import (
    ToolApprovalRequiredError,
    ToolArgumentsInvalidError,
    ToolInvocation,
    ToolRegistry,
    ToolSnapshotStaleError,
    ToolSpec,
)
from agent.tool_management.registry import ToolHandler
from domain.models import Base, Task, TaskStatus, ToolLog, User


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
