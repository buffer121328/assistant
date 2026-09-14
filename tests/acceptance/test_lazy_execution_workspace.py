from __future__ import annotations

from collections.abc import AsyncIterator
from hashlib import sha256
import json
from pathlib import Path

import anyio
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from application.artifact_lifecycle import ArtifactLifecycleService
from application.session_context.conversations import ConversationService
from application.session_context.execution_workspaces import ExecutionWorkspaceService
from application.session_context.resource_references import (
    ResourceReferenceError,
    ResourceReferenceService,
)
from application.task_execution.executor import TaskExecutionService
from application.task_execution.lifecycle import TaskService
from domain.models import Base, Task, User
from domain.policies.enterprise import (
    GovernedAgentProfile,
    PROFILE_SCHEMA_VERSION,
    SubjectContext,
)
from infrastructure.settings.config import Settings
from tools.builtin.artifacts import ArtifactStore
from tools.builtin.workspace import SessionWorkspaceStore
from workers.runtime import execute_task_by_id


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create isolated persistence for lazy workspace acceptance scenarios."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/lazy-workspace.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_user(
    sessionmaker: async_sessionmaker[AsyncSession], display_name: str
) -> User:
    """Persist one local owner without external identity dependencies."""
    async with sessionmaker() as session:
        user = User(display_name=display_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


class StaticProfileResolver:
    """Resolve one deterministic profile without network or model calls."""

    def __init__(self, session: AsyncSession, profile: GovernedAgentProfile) -> None:
        """Bind profile persistence to the execution transaction."""
        self.session = session
        self.profile = profile

    async def resolve_for_task(self, task: Task) -> GovernedAgentProfile:
        """Persist the same bounded profile snapshot as production resolution."""
        task.agent_profile_schema_version = PROFILE_SCHEMA_VERSION
        task.agent_profile_snapshot = self.profile.to_snapshot()
        await self.session.flush()
        return self.profile


async def execute_successfully(session: AsyncSession, task_id: str) -> Task:
    """Complete a fake Agent task without invoking any external runtime."""
    task = await session.get(Task, task_id)
    assert task is not None
    task.status = "success"
    task.result_text = "done"
    await session.commit()
    await session.refresh(task)
    return task


async def create_resource_task(
    session: AsyncSession,
    *,
    user: User,
    source: Path,
) -> tuple[Task, str, str]:
    """Create a Conversation, attach one file, and snapshot it into a Task."""
    conversation = await ConversationService(session).create(
        user_id=user.id,
        title="Workspace",
    )
    reference = await ResourceReferenceService(session).attach_local_file(
        conversation_id=conversation.id,
        user_id=user.id,
        source_ref=str(source),
    )
    task = await TaskService(session).create_task(
        user_id=user.id,
        platform="local",
        task_type="office",
        input_text="use selected file",
        conversation_id=conversation.id,
        resource_reference_ids=(reference.id,),
    )
    return task, conversation.id, reference.id


def test_plain_text_local_submission_does_not_create_conversation_workspace(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Submitting a text-only task has no eager Conversation filesystem side effect."""
    owner = anyio.run(create_user, sessionmaker, "Lazy API Owner")
    workspace_root = tmp_path / "sessions"
    app = create_app(
        Settings(
            redis_url="redis://placeholder",
            session_workspace_root=workspace_root,
        )
    )
    app.state.db_sessionmaker = sessionmaker

    with TestClient(app) as client:
        response = client.post(
            "/local/tasks",
            json={
                "user_id": owner.id,
                "task_type": "office",
                "input_text": "plain text only",
            },
        )
        assert response.status_code == 201
        task_id = response.json()["task"]["task_id"]
        continued = client.post(
            f"/local/tasks/{task_id}/messages",
            json={
                "user_id": owner.id,
                "content": "plain follow-up",
            },
        )

    assert continued.status_code == 200
    conversation_id = response.json()["task"]["conversation_id"]
    assert conversation_id
    assert continued.json()["task"]["conversation_id"] == conversation_id
    assert not (workspace_root / conversation_id).exists()


@pytest.mark.asyncio
async def test_plain_text_execution_remains_workspace_free(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Shared execution does not create a workspace when the snapshot has no files."""
    owner = await create_user(sessionmaker, "Lazy Execution Owner")
    workspace_root = tmp_path / "sessions"
    async with sessionmaker() as session:
        conversation = await ConversationService(session).create(user_id=owner.id)
        task = await TaskService(session).create_task(
            user_id=owner.id,
            platform="local",
            task_type="office",
            input_text="plain text execution",
            conversation_id=conversation.id,
        )
        profile = GovernedAgentProfile(
            subject=SubjectContext(tenant_id=task.tenant_id, user_id=owner.id),
            capabilities=("office.text@1.0",),
        )

        async def fake_agent(task_id: str) -> Task:
            """Complete the selected Task without tools."""
            return await execute_successfully(session, task_id)

        result = await TaskExecutionService(
            session,
            agent_task_executor=fake_agent,
            settings=Settings(session_workspace_root=workspace_root),
            governed_profile_resolver=StaticProfileResolver(session, profile),  # type: ignore[arg-type]
        ).execute_task(task.id)

    assert result.status == "success"
    assert not (workspace_root / conversation.id).exists()


@pytest.mark.asyncio
async def test_execution_materializes_snapshot_resource_and_safe_manifest(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Execution copies selected bytes once and records only safe relative facts."""
    owner = await create_user(sessionmaker, "Materialize Owner")
    workspace_root = tmp_path / "sessions"
    sandbox_root = tmp_path / "sandbox"
    source = tmp_path / "quarterly-report.txt"
    content = b"governed office input"
    source.write_bytes(content)
    second_source = tmp_path / "budget-notes.txt"
    second_content = b"second governed input"
    second_source.write_bytes(second_content)

    async with sessionmaker() as session:
        _first_task, conversation_id, reference_id = await create_resource_task(
            session,
            user=owner,
            source=source,
        )
        second_reference = await ResourceReferenceService(session).attach_local_file(
            conversation_id=conversation_id,
            user_id=owner.id,
            source_ref=str(second_source),
        )
        task = await TaskService(session).create_task(
            user_id=owner.id,
            platform="local",
            task_type="office",
            input_text="use selected files in order",
            conversation_id=conversation_id,
            resource_reference_ids=(second_reference.id, reference_id),
        )
        profile = GovernedAgentProfile(
            subject=SubjectContext(tenant_id=task.tenant_id, user_id=owner.id),
            capabilities=("office.file@1.0",),
        )
        calls: list[str] = []

        async def fake_agent(task_id: str) -> Task:
            """Record that preparation completed before Agent execution."""
            calls.append(task_id)
            return await execute_successfully(session, task_id)

        result = await TaskExecutionService(
            session,
            agent_task_executor=fake_agent,
            settings=Settings(
                session_workspace_root=workspace_root,
                sandbox_workspace_root=sandbox_root,
            ),
            governed_profile_resolver=StaticProfileResolver(session, profile),  # type: ignore[arg-type]
        ).execute_task(task.id)
        retry = await ExecutionWorkspaceService(
            session,
            root=workspace_root,
        ).prepare(task)

    session_root = workspace_root / conversation_id
    materialized = session_root / "input" / reference_id / source.name
    second_materialized = (
        session_root / "input" / second_reference.id / second_source.name
    )
    manifest_path = session_root / "manifest.json"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)

    assert result.status == "success"
    assert calls == [task.id]
    assert retry.state == "active"
    assert materialized.read_bytes() == content
    assert second_materialized.read_bytes() == second_content
    assert manifest["resource_order"] == [second_reference.id, reference_id]
    assert manifest["resources"][second_reference.id] == {
        "content_hash": sha256(second_content).hexdigest(),
        "relative_path": f"input/{second_reference.id}/{second_source.name}",
        "size_bytes": len(second_content),
        "version": retry.resources[0].version,
    }
    assert manifest["resources"][reference_id] == {
        "content_hash": sha256(content).hexdigest(),
        "relative_path": f"input/{reference_id}/{source.name}",
        "size_bytes": len(content),
        "version": retry.resources[1].version,
    }
    assert manifest["tasks"][task.id]["state"] == "success"
    assert manifest["tasks"][task.id]["workspace_need"] is True
    assert manifest["tasks"][task.id]["artifact_ids"] == []
    assert manifest["tasks"][task.id]["cleanup_state"] == "retaining"
    assert manifest["tasks"][task.id]["cleanup_policy"] == "success"
    assert str(source) not in manifest_text
    assert "use selected files in order" not in manifest_text
    assert not sandbox_root.exists()
    assert set((session_root / "input").iterdir()) == {
        session_root / "input" / second_reference.id,
        session_root / "input" / reference_id,
    }


@pytest.mark.asyncio
async def test_workspace_cleanup_preserves_inputs_and_registered_artifact(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Success cleanup removes temporary files without deleting governed bytes."""
    owner = await create_user(sessionmaker, "Cleanup Owner")
    workspace_root = tmp_path / "sessions"
    artifacts_root = tmp_path / "artifacts"
    source = tmp_path / "source.txt"
    source.write_text("source bytes", encoding="utf-8")

    async with sessionmaker() as session:
        task, conversation_id, reference_id = await create_resource_task(
            session,
            user=owner,
            source=source,
        )
        workspace = ExecutionWorkspaceService(
            session,
            root=workspace_root,
            artifacts_root=artifacts_root,
        )
        await workspace.prepare(task)
        store = SessionWorkspaceStore(workspace_root)
        store.reserve_task_work(
            session_id=conversation_id,
            task_id=task.id,
            filename="draft.txt",
        ).write_text("temporary draft", encoding="utf-8")
        store.reserve_task_output(
            session_id=conversation_id,
            task_id=task.id,
            filename="result.txt",
        ).write_text("temporary result", encoding="utf-8")
        store.reserve_task_audit(
            session_id=conversation_id,
            task_id=task.id,
            filename="trace.txt",
        ).write_text("temporary trace", encoding="utf-8")
        artifact = await ArtifactLifecycleService(
            session,
            store=ArtifactStore(artifacts_root),
        ).register_bytes(
            task_id=task.id,
            actor_user_id=owner.id,
            filename="result.txt",
            media_type="text/plain",
            data=b"durable artifact",
            generation_method="office.export",
            idempotency_key="result-v1",
        )
        task.status = "success"
        await session.commit()

        await workspace.associate_artifact(task, artifact_id=artifact.id)
        await workspace.cleanup_task(task, decision="success")
        await workspace.cleanup_task(task, decision="success")
        download = await ArtifactLifecycleService(
            session,
            store=ArtifactStore(artifacts_root),
        ).resolve_download(
            artifact_id=artifact.id,
            actor_user_id=owner.id,
        )

    session_root = workspace_root / conversation_id
    manifest = json.loads((session_root / "manifest.json").read_text(encoding="utf-8"))
    task_manifest = manifest["tasks"][task.id]

    assert not (session_root / "work" / task.id).exists()
    assert not (session_root / "output" / task.id).exists()
    assert not (session_root / "audit" / task.id).exists()
    assert (session_root / "input" / reference_id / source.name).read_text(
        encoding="utf-8"
    ) == "source bytes"
    assert task_manifest["workspace_need"] is True
    assert task_manifest["artifact_ids"] == [artifact.id]
    assert task_manifest["cleanup_state"] == "cleaned"
    assert task_manifest["cleanup_policy"] == "success"
    assert download.path.read_bytes() == b"durable artifact"


@pytest.mark.asyncio
async def test_diagnostic_retention_removes_work_and_output_only(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Failed-task cleanup can retain only its Task-scoped audit diagnostics."""
    owner = await create_user(sessionmaker, "Diagnostic Owner")
    workspace_root = tmp_path / "sessions"
    source = tmp_path / "source.txt"
    source.write_text("source bytes", encoding="utf-8")

    async with sessionmaker() as session:
        task, conversation_id, _reference_id = await create_resource_task(
            session,
            user=owner,
            source=source,
        )
        workspace = ExecutionWorkspaceService(session, root=workspace_root)
        await workspace.prepare(task)
        store = SessionWorkspaceStore(workspace_root)
        store.reserve_task_work(
            session_id=conversation_id,
            task_id=task.id,
            filename="draft.txt",
        ).write_text("temporary draft", encoding="utf-8")
        store.reserve_task_output(
            session_id=conversation_id,
            task_id=task.id,
            filename="result.txt",
        ).write_text("temporary result", encoding="utf-8")
        audit_path = store.reserve_task_audit(
            session_id=conversation_id,
            task_id=task.id,
            filename="trace.txt",
        )
        audit_path.write_text("bounded diagnostic", encoding="utf-8")
        task.status = "failed"
        task.error_message = "bounded failure"
        await session.commit()

        await workspace.cleanup_task(task, decision="diagnostic_retained")

    session_root = workspace_root / conversation_id
    manifest = json.loads((session_root / "manifest.json").read_text(encoding="utf-8"))
    task_manifest = manifest["tasks"][task.id]

    assert not (session_root / "work" / task.id).exists()
    assert not (session_root / "output" / task.id).exists()
    assert audit_path.read_text(encoding="utf-8") == "bounded diagnostic"
    assert task_manifest["state"] == "failed"
    assert task_manifest["cleanup_state"] == "diagnostic_retained"
    assert task_manifest["cleanup_policy"] == "diagnostic_retained"
    assert task_manifest["diagnostic"] == {"error_present": True}


@pytest.mark.asyncio
async def test_same_conversation_tasks_reserve_isolated_output_paths(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Two selected-resource Tasks cannot collide on a common output filename."""
    owner = await create_user(sessionmaker, "Concurrent Owner")
    workspace_root = tmp_path / "sessions"
    source = tmp_path / "source.txt"
    source.write_text("source bytes", encoding="utf-8")

    async with sessionmaker() as session:
        first, conversation_id, reference_id = await create_resource_task(
            session,
            user=owner,
            source=source,
        )
        second = await TaskService(session).create_task(
            user_id=owner.id,
            platform="local",
            task_type="office",
            input_text="use the same selected file",
            conversation_id=conversation_id,
            resource_reference_ids=(reference_id,),
        )
        workspace = ExecutionWorkspaceService(session, root=workspace_root)
        await workspace.prepare(first)
        await workspace.prepare(second)
        store = SessionWorkspaceStore(workspace_root)
        first_output = store.reserve_task_output(
            session_id=conversation_id,
            task_id=first.id,
            filename="report.txt",
        )
        second_output = store.reserve_task_output(
            session_id=conversation_id,
            task_id=second.id,
            filename="report.txt",
        )
        first_output.write_text("first", encoding="utf-8")
        second_output.write_text("second", encoding="utf-8")

    assert first_output != second_output
    assert first_output.read_text(encoding="utf-8") == "first"
    assert second_output.read_text(encoding="utf-8") == "second"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["changed", "missing", "removed"])
async def test_stale_or_removed_resource_blocks_execution_and_valid_target(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    mutation: str,
) -> None:
    """Execution reauthorization fails closed before invoking the Agent callback."""
    owner = await create_user(sessionmaker, f"Blocked {mutation}")
    workspace_root = tmp_path / f"sessions-{mutation}"
    source = tmp_path / f"{mutation}.txt"
    source.write_text("version one", encoding="utf-8")

    async with sessionmaker() as session:
        task, conversation_id, reference_id = await create_resource_task(
            session,
            user=owner,
            source=source,
        )
        if mutation == "changed":
            source.write_text("version two with a different size", encoding="utf-8")
        elif mutation == "missing":
            source.unlink()
        else:
            await ResourceReferenceService(session).remove(
                conversation_id=conversation_id,
                reference_id=reference_id,
                user_id=owner.id,
            )
        profile = GovernedAgentProfile(
            subject=SubjectContext(tenant_id=task.tenant_id, user_id=owner.id),
        )
        calls: list[str] = []

        async def forbidden_agent(task_id: str) -> Task:
            """Fail the test if unverified bytes reach Agent execution."""
            calls.append(task_id)
            raise AssertionError("Agent execution must not start")

        with pytest.raises(ResourceReferenceError):
            await TaskExecutionService(
                session,
                agent_task_executor=forbidden_agent,
                settings=Settings(session_workspace_root=workspace_root),
                governed_profile_resolver=StaticProfileResolver(session, profile),  # type: ignore[arg-type]
            ).execute_task(task.id)

    assert calls == []
    target = workspace_root / conversation_id / "input" / reference_id / source.name
    assert not target.exists()
    assert not list(workspace_root.rglob("*.tmp")) if workspace_root.exists() else True


@pytest.mark.asyncio
async def test_cross_scope_snapshot_resource_is_rejected_before_copy(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """A reference whose persisted owner scope changes cannot be materialized."""
    owner = await create_user(sessionmaker, "Original Owner")
    other = await create_user(sessionmaker, "Changed Owner")
    source = tmp_path / "private.txt"
    source.write_text("private", encoding="utf-8")

    async with sessionmaker() as session:
        task, _conversation_id, reference_id = await create_resource_task(
            session,
            user=owner,
            source=source,
        )
        from domain.models import ConversationResourceReference

        reference = await session.get(ConversationResourceReference, reference_id)
        assert reference is not None
        reference.user_id = other.id
        reference.owner_id = other.id
        await session.commit()

        with pytest.raises(ResourceReferenceError) as failure:
            await ExecutionWorkspaceService(
                session,
                root=tmp_path / "sessions",
            ).prepare(task)

    assert failure.value.code == "resource_reference_not_found"
    assert not (tmp_path / "sessions" / str(task.conversation_id)).exists()


@pytest.mark.asyncio
async def test_worker_records_stale_preparation_as_bounded_task_failure(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """The worker failure boundary records preparation errors without running tools."""
    owner = await create_user(sessionmaker, "Worker Stale Owner")
    source = tmp_path / "worker-stale.txt"
    source.write_text("queued version", encoding="utf-8")
    async with sessionmaker() as session:
        task, _conversation_id, _reference_id = await create_resource_task(
            session,
            user=owner,
            source=source,
        )
    source.write_text("changed after queueing with a different size", encoding="utf-8")

    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/lazy-workspace.db",
            session_workspace_root=tmp_path / "sessions",
        ),
    )

    assert result.status == "failed"
    assert result.error_message == "resource_reference_stale"
    assert not (tmp_path / "sessions" / str(task.conversation_id)).exists()
