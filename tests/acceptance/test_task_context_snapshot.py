from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from application.session_context.context_snapshots import TaskContextSnapshotService
from application.session_context.conversations import ConversationError, ConversationService
from application.task_execution.executor import TaskExecutionService
from application.task_execution.lifecycle import TaskService
from domain.models import Base, Task, TaskContextSnapshot, User
from domain.policies.enterprise import (
    GovernedAgentProfile,
    GovernanceValidationError,
    PROFILE_SCHEMA_VERSION,
    SubjectContext,
)
from infrastructure.settings.config import Settings


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create an isolated database containing the current domain metadata."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/context.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_user(
    sessionmaker: async_sessionmaker[AsyncSession], display_name: str
) -> User:
    """Persist one test user without relying on external identity providers."""
    async with sessionmaker() as session:
        user = User(display_name=display_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.mark.asyncio
async def test_task_creation_snapshots_owned_scope_and_archived_rejection_is_atomic(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """An active owned conversation creates one snapshot; archived input creates none."""
    user = await create_user(sessionmaker, "Context Owner")
    async with sessionmaker() as session:
        conversation = await ConversationService(session).create(user_id=user.id)
        task = await TaskService(session).create_task(
            user_id=user.id,
            platform="local",
            task_type="office",
            input_text="summarize the attached report",
            conversation_id=conversation.id,
        )
        snapshot = await session.scalar(
            select(TaskContextSnapshot).where(TaskContextSnapshot.task_id == task.id)
        )
        assert snapshot is not None
        assert snapshot.conversation_id == conversation.id
        assert snapshot.user_id == task.user_id
        assert snapshot.tenant_id == task.tenant_id
        assert snapshot.organization_id == task.organization_id
        assert snapshot.owner_type == task.owner_type
        assert snapshot.owner_id == task.owner_id
        assert snapshot.visibility == task.visibility
        assert snapshot.state == "initial"

        await ConversationService(session).archive(
            conversation_id=conversation.id, user_id=user.id
        )
        task_count = int(await session.scalar(select(func.count()).select_from(Task)) or 0)
        snapshot_count = int(
            await session.scalar(select(func.count()).select_from(TaskContextSnapshot))
            or 0
        )
        with pytest.raises(ConversationError):
            await TaskService(session).create_task(
                user_id=user.id,
                platform="local",
                task_type="office",
                input_text="must not persist",
                conversation_id=conversation.id,
            )
        assert int(await session.scalar(select(func.count()).select_from(Task)) or 0) == task_count
        assert (
            int(
                await session.scalar(
                    select(func.count()).select_from(TaskContextSnapshot)
                )
                or 0
            )
            == snapshot_count
        )


class StaticProfileResolver:
    """Persist one deterministic governed profile for execution-boundary tests."""

    def __init__(self, session: AsyncSession, profile: GovernedAgentProfile) -> None:
        """Bind the fake resolver to the same transaction as task execution."""
        self.session = session
        self.profile = profile

    async def resolve_for_task(self, task: Task) -> GovernedAgentProfile:
        """Mirror the production resolver's bounded Task snapshot side effect."""
        task.agent_profile_schema_version = PROFILE_SCHEMA_VERSION
        task.agent_profile_snapshot = self.profile.to_snapshot()
        await self.session.flush()
        return self.profile


class FailingProfileResolver:
    """Fail governance resolution before Agent execution starts."""

    async def resolve_for_task(self, task: Task) -> GovernedAgentProfile:
        """Raise a safe validation error without mutating the Task."""
        del task
        raise GovernanceValidationError("profile unavailable")


@pytest.mark.asyncio
async def test_governed_execution_finalizes_context_once_and_failure_stays_initial(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Finalization is bounded and idempotent, while a failed resolver cannot finalize."""
    user = await create_user(sessionmaker, "Governed Context Owner")
    async with sessionmaker() as session:
        task = await TaskService(session).create_task(
            user_id=user.id,
            platform="api",
            task_type="office",
            input_text="analyze",
        )
        profile = GovernedAgentProfile(
            subject=SubjectContext(tenant_id=task.tenant_id, user_id=user.id),
            capabilities=("office.spreadsheet@1.0",),
            tools=("sheet.read",),
            knowledge_scopes=("company.public",),
        )

        async def execute_agent(task_id: str) -> Task:
            """Return the persisted Task without invoking a model or tool."""
            current = await session.get(Task, task_id)
            assert current is not None
            return current

        await TaskExecutionService(
            session,
            agent_task_executor=execute_agent,
            governed_profile_resolver=StaticProfileResolver(session, profile),  # type: ignore[arg-type]
        ).execute_task(task.id)
        snapshot = await TaskContextSnapshotService(session).get_owned(
            task_id=task.id, user_id=user.id
        )
        assert snapshot.state == "finalized"
        assert snapshot.agent_profile_snapshot == profile.to_snapshot()
        assert snapshot.capability_snapshot == ("office.spreadsheet@1.0",)
        assert snapshot.knowledge_scope_snapshot == ("company.public",)
        assert snapshot.resource_reference_ids == ()
        assert snapshot.resolved_resource_versions == {}
        finalized_at = snapshot.finalized_at

        replacement = GovernedAgentProfile(
            subject=SubjectContext(tenant_id=task.tenant_id, user_id=user.id),
            capabilities=("office.changed@2.0",),
        )
        await TaskExecutionService(
            session,
            agent_task_executor=execute_agent,
            governed_profile_resolver=StaticProfileResolver(session, replacement),  # type: ignore[arg-type]
        ).execute_task(task.id)
        unchanged = await TaskContextSnapshotService(session).get_owned(
            task_id=task.id, user_id=user.id
        )
        assert unchanged.capability_snapshot == ("office.spreadsheet@1.0",)
        assert unchanged.finalized_at == finalized_at

        failed = await TaskService(session).create_task(
            user_id=user.id,
            platform="api",
            task_type="office",
            input_text="fail safely",
        )
        with pytest.raises(GovernanceValidationError):
            await TaskExecutionService(
                session,
                agent_task_executor=execute_agent,
                governed_profile_resolver=FailingProfileResolver(),  # type: ignore[arg-type]
            ).execute_task(failed.id)
        failed_snapshot = await TaskContextSnapshotService(session).get_owned(
            task_id=failed.id, user_id=user.id
        )
        assert failed_snapshot.state == "initial"
        assert failed_snapshot.finalized_at is None


def test_local_context_api_is_owner_scoped_and_omits_sensitive_storage(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """The local API returns bounded metadata only to the Task owner."""
    import anyio

    owner = anyio.run(create_user, sessionmaker, "API Owner")
    other = anyio.run(create_user, sessionmaker, "API Other")
    app = create_app(
        Settings(
            redis_url="redis://placeholder",
            session_workspace_root=tmp_path / "workspace",
        )
    )
    app.state.db_sessionmaker = sessionmaker
    with TestClient(app) as client:
        created = client.post(
            "/local/tasks",
            json={
                "user_id": owner.id,
                "task_type": "office",
                "input_text": "create bounded context",
            },
        )
        assert created.status_code == 201
        task_id = created.json()["task"]["task_id"]
        visible = client.get(
            f"/local/tasks/{task_id}/context", params={"user_id": owner.id}
        )
        hidden = client.get(
            f"/local/tasks/{task_id}/context", params={"user_id": other.id}
        )

    assert visible.status_code == 200
    payload = visible.json()
    assert payload["task_id"] == task_id
    assert payload["user_id"] == owner.id
    assert payload["state"] == "initial"
    assert payload["resource_reference_ids"] == []
    assert payload["resolved_resource_versions"] == {}
    assert "agent_profile_snapshot" not in payload
    assert "input_text" not in payload
    assert "path" not in visible.text.lower()
    assert hidden.status_code == 404
    assert owner.id not in hidden.text


def test_context_snapshot_migration_is_linear_and_backfills_existing_tasks() -> None:
    """The additive migration follows the current head and contains a set-based backfill."""
    migration = (
        Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/202608110001_task_context_snapshots.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "202608110001"' in migration
    assert 'down_revision: str | None = "202608100005"' in migration
    assert "INSERT INTO task_context_snapshots" in migration
    assert "FROM tasks" in migration
    assert "ALTER TABLE tasks NO FORCE ROW LEVEL SECURITY" in migration
    assert "ALTER TABLE tasks FORCE ROW LEVEL SECURITY" in migration
    assert "DROP TABLE task_context_snapshots" not in migration
