from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from application.artifact_lifecycle import ArtifactLifecycleService
from domain.models import Base, Conversation, Task, TaskContextSnapshot, User
from infrastructure.settings.config import Settings
from tools.builtin.artifacts import ArtifactStore


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create isolated persistence for non-local Resource Reference coverage."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/nonlocal-resources.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def users(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[User, User]:
    """Persist two isolated owners for scope assertions."""
    async with sessionmaker() as session:
        owner = User(display_name="Non-local owner")
        other = User(display_name="Non-local other")
        session.add_all([owner, other])
        await session.commit()
        await session.refresh(owner)
        await session.refresh(other)
        return owner, other


@pytest_asyncio.fixture
async def owner_conversation(
    sessionmaker: async_sessionmaker[AsyncSession], users: tuple[User, User]
) -> tuple[str, str]:
    """Create one owned Conversation and an existing Task for continuation tests."""
    owner, _other = users
    async with sessionmaker() as session:
        conversation = Conversation(user_id=owner.id, title="Resources", channel="desktop")
        session.add(conversation)
        await session.flush()
        task = Task(
            user_id=owner.id,
            owner_id=owner.id,
            platform="local",
            task_type="chat",
            input_text="prepare context",
            status="success",
            conversation_id=conversation.id,
        )
        session.add(task)
        await session.flush()
        session.add(
            TaskContextSnapshot(
                task_id=task.id,
                conversation_id=conversation.id,
                user_id=owner.id,
                tenant_id=task.tenant_id,
                organization_id=task.organization_id,
                owner_type=task.owner_type,
                owner_id=task.owner_id,
                visibility=task.visibility,
                state="finalized",
                resource_reference_ids_json="[]",
                resolved_resource_versions_json="{}",
                memory_scope_snapshot_json="[]",
                knowledge_scope_snapshot_json="[]",
                capability_snapshot_json="[]",
            )
        )
        await session.commit()
        return conversation.id, task.id


@pytest_asyncio.fixture
async def registered_artifact(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    users: tuple[User, User],
    owner_conversation: tuple[str, str],
):
    """Register one governed Artifact in the same owner and Conversation scope."""
    owner, _other = users
    _conversation_id, task_id = owner_conversation
    async with sessionmaker() as session:
        return await ArtifactLifecycleService(
            session, store=ArtifactStore(tmp_path / "artifacts")
        ).register_bytes(
            task_id=task_id,
            actor_user_id=owner.id,
            filename="brief.txt",
            media_type="text/plain",
            data=b"governed artifact",
            generation_method="test.fixture",
            idempotency_key="nonlocal-artifact",
        )


@pytest_asyncio.fixture
async def client(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> AsyncIterator[TestClient]:
    """Create a local API client with isolated managed roots."""
    app = create_app(
        Settings(
            redis_url="redis://placeholder",
            artifacts_root=tmp_path / "artifacts",
            resource_uploads_root=tmp_path / "resource-inputs",
            session_workspace_root=tmp_path / "workspace",
        )
    )
    app.state.db_sessionmaker = sessionmaker
    with TestClient(app) as test_client:
        yield test_client


def test_uploaded_file_is_managed_path_free_and_snapshot_verified(
    client: TestClient,
    users: tuple[User, User],
    owner_conversation: tuple[str, str],
    tmp_path: Path,
) -> None:
    """Uploads stay managed while their stable IDs bind the next Task snapshot."""
    owner, other = users
    conversation_id, task_id = owner_conversation
    upload = client.post(
        f"/local/conversations/{conversation_id}/resources/upload",
        data={"user_id": owner.id},
        files={"file": ("budget.csv", b"amount\n42\n", "text/csv")},
    )
    denied = client.post(
        f"/local/conversations/{conversation_id}/resources/upload",
        data={"user_id": other.id},
        files={"file": ("budget.csv", b"amount\n42\n", "text/csv")},
    )
    listed = client.get(
        f"/local/conversations/{conversation_id}/resources", params={"user_id": owner.id}
    )
    assert upload.status_code == 201, upload.text
    assert denied.status_code == 404
    item = upload.json()
    assert item["resource_kind"] == "uploaded_file"
    assert item["display_name"] == "budget.csv"
    assert "resource-inputs" not in str(item)
    assert "source_ref" not in item
    assert listed.json()["items"][0]["reference_id"] == item["reference_id"]

    continued = client.post(
        f"/local/tasks/{task_id}/messages",
        json={
            "user_id": owner.id,
            "content": "use the uploaded budget",
            "resource_reference_ids": [item["reference_id"]],
        },
    )
    assert continued.status_code == 200, continued.text
    snapshot = client.get(
        f"/local/tasks/{continued.json()['task']['task_id']}/context",
        params={"user_id": owner.id},
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["resource_reference_ids"] == [item["reference_id"]]
    assert snapshot.json()["resolved_resource_versions"][item["reference_id"]] == item["version"]
    assert not list((tmp_path / "resource-inputs").rglob("budget.csv"))


def test_artifact_reference_is_owner_scoped_and_snapshotted(
    client: TestClient,
    users: tuple[User, User],
    owner_conversation: tuple[str, str],
    registered_artifact,
) -> None:
    """Artifact references reuse the owning Conversation lifecycle boundary."""
    owner, other = users
    conversation_id, task_id = owner_conversation
    attached = client.post(
        f"/local/conversations/{conversation_id}/resources/artifact",
        json={"user_id": owner.id, "artifact_id": registered_artifact.id},
    )
    denied = client.post(
        f"/local/conversations/{conversation_id}/resources/artifact",
        json={"user_id": other.id, "artifact_id": registered_artifact.id},
    )
    assert attached.status_code == 201, attached.text
    assert denied.status_code == 404
    item = attached.json()
    assert item["resource_kind"] == "artifact"
    assert item["version"] == registered_artifact.version
    assert "storage_reference" not in str(item)

    continued = client.post(
        f"/local/tasks/{task_id}/messages",
        json={
            "user_id": owner.id,
            "content": "use the registered artifact",
            "resource_reference_ids": [item["reference_id"]],
        },
    )
    assert continued.status_code == 200, continued.text
    snapshot = client.get(
        f"/local/tasks/{continued.json()['task']['task_id']}/context",
        params={"user_id": owner.id},
    )
    assert snapshot.json()["resolved_resource_versions"][item["reference_id"]] == registered_artifact.version


def test_external_record_is_safe_but_fails_closed_without_resolver(
    client: TestClient,
    users: tuple[User, User],
    owner_conversation: tuple[str, str],
) -> None:
    """No external identity is treated as readable without a runtime resolver."""
    owner, _other = users
    conversation_id, task_id = owner_conversation
    attached = client.post(
        f"/local/conversations/{conversation_id}/resources/external-record",
        json={
            "user_id": owner.id,
            "provider": "crm",
            "external_resource_id": "record-42",
            "display_name": "Account 42",
            "version": "etag-1",
        },
    )
    assert attached.status_code == 201, attached.text
    item = attached.json()
    assert item["resource_kind"] == "external_record"
    assert "record-42" not in str(item)

    continued = client.post(
        f"/local/tasks/{task_id}/messages",
        json={
            "user_id": owner.id,
            "content": "use the external account",
            "resource_reference_ids": [item["reference_id"]],
        },
    )
    assert continued.status_code == 409
    assert continued.json()["error"]["code"] == "resource_reference_unavailable"
