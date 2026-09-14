from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from domain.models import Base, ConversationMessage, Task, TaskContextSnapshot, User
from infrastructure.settings.config import Settings


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create isolated persistence for Resource Reference acceptance tests."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/resources.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_user(
    sessionmaker: async_sessionmaker[AsyncSession], display_name: str
) -> User:
    """Persist a local test owner without any external identity dependency."""
    async with sessionmaker() as session:
        user = User(display_name=display_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.fixture
def client(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> TestClient:
    """Create a local API client whose incidental workspace root is temporary."""
    app = create_app(
        Settings(
            redis_url="redis://placeholder",
            session_workspace_root=tmp_path / "workspace",
        )
    )
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


def create_conversation(client: TestClient, user_id: str, title: str) -> str:
    """Create one active Conversation and return its stable identifier."""
    response = client.post(
        "/api/conversations", json={"user_id": user_id, "title": title}
    )
    assert response.status_code == 201
    return str(response.json()["conversation_id"])


def attach_file(
    client: TestClient,
    *,
    conversation_id: str,
    user_id: str,
    path: Path,
) -> dict[str, object]:
    """Attach one explicit local file through the public local API."""
    response = client.post(
        f"/local/conversations/{conversation_id}/resources",
        json={
            "user_id": user_id,
            "resource_kind": "local_file",
            "source_ref": str(path),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_attach_list_remove_and_mention_search_hide_local_paths(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Resource lifecycle is idempotent, searchable, and path-safe."""
    owner = anyio.run(create_user, sessionmaker, "Resource Owner")
    conversation_id = create_conversation(client, owner.id, "Budget")
    source = tmp_path / "预算报告.xlsx"
    source.write_text("not opened by attachment", encoding="utf-8")

    first = attach_file(
        client,
        conversation_id=conversation_id,
        user_id=owner.id,
        path=source,
    )
    second = attach_file(
        client,
        conversation_id=conversation_id,
        user_id=owner.id,
        path=source,
    )
    listed = client.get(
        f"/local/conversations/{conversation_id}/resources",
        params={"user_id": owner.id},
    )
    mentions = client.get(
        "/local/mentions",
        params={
            "user_id": owner.id,
            "conversation_id": conversation_id,
            "query": "报告",
        },
    )

    assert first["reference_id"] == second["reference_id"]
    assert first["display_name"] == source.name
    assert first["resource_kind"] == "local_file"
    assert first["status"] == "attached"
    size_bytes = first["size_bytes"]
    assert isinstance(size_bytes, int)
    assert size_bytes == source.stat().st_size
    assert first["version"]
    assert "source_ref" not in first
    assert str(source) not in listed.text
    assert [item["reference_id"] for item in listed.json()["items"]] == [
        first["reference_id"]
    ]
    assert [item["reference_id"] for item in mentions.json()["items"]] == [
        first["reference_id"]
    ]

    removed = client.delete(
        f"/local/conversations/{conversation_id}/resources/{first['reference_id']}",
        params={"user_id": owner.id},
    )
    after = client.get(
        f"/local/conversations/{conversation_id}/resources",
        params={"user_id": owner.id},
    )
    assert removed.status_code == 200
    assert removed.json()["status"] == "removed"
    assert after.json()["items"] == []
    assert source.exists()


def test_invalid_archived_and_cross_owner_resource_access_is_rejected(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Unsafe paths and foreign or archived Conversations reveal no metadata."""
    owner = anyio.run(create_user, sessionmaker, "Owner")
    other = anyio.run(create_user, sessionmaker, "Other")
    owner_conversation = create_conversation(client, owner.id, "Owner Files")
    other_conversation = create_conversation(client, other.id, "Other Files")
    owner_dir = tmp_path / "owner"
    other_dir = tmp_path / "other"
    owner_dir.mkdir()
    other_dir.mkdir()
    owner_file = owner_dir / "report.docx"
    other_file = other_dir / "report.docx"
    owner_file.write_text("owner", encoding="utf-8")
    other_file.write_text("other", encoding="utf-8")
    owner_ref = attach_file(
        client,
        conversation_id=owner_conversation,
        user_id=owner.id,
        path=owner_file,
    )
    other_ref = attach_file(
        client,
        conversation_id=other_conversation,
        user_id=other.id,
        path=other_file,
    )

    cross_list = client.get(
        f"/local/conversations/{owner_conversation}/resources",
        params={"user_id": other.id},
    )
    cross_remove = client.delete(
        f"/local/conversations/{owner_conversation}/resources/{owner_ref['reference_id']}",
        params={"user_id": other.id},
    )
    search = client.get(
        "/local/mentions",
        params={
            "user_id": owner.id,
            "conversation_id": owner_conversation,
            "query": "report",
        },
    )
    assert cross_list.status_code == 404
    assert cross_remove.status_code == 404
    assert [item["reference_id"] for item in search.json()["items"]] == [
        owner_ref["reference_id"]
    ]
    assert other_ref["reference_id"] not in search.text

    missing = client.post(
        f"/local/conversations/{owner_conversation}/resources",
        json={
            "user_id": owner.id,
            "resource_kind": "local_file",
            "source_ref": str(tmp_path / "missing.txt"),
        },
    )
    directory = client.post(
        f"/local/conversations/{owner_conversation}/resources",
        json={
            "user_id": owner.id,
            "resource_kind": "local_file",
            "source_ref": str(owner_dir),
        },
    )
    symlink_path = tmp_path / "linked-report.docx"
    symlink_path.symlink_to(owner_file)
    symlink = client.post(
        f"/local/conversations/{owner_conversation}/resources",
        json={
            "user_id": owner.id,
            "resource_kind": "local_file",
            "source_ref": str(symlink_path),
        },
    )
    malformed = client.post(
        f"/local/conversations/{owner_conversation}/resources",
        json={
            "user_id": owner.id,
            "resource_kind": "local_file",
            "source_ref": "invalid\x00path",
        },
    )
    assert missing.status_code == 422
    assert directory.status_code == 422
    assert symlink.status_code == 422
    assert malformed.status_code == 422
    assert str(tmp_path) not in (
        missing.text + directory.text + symlink.text + malformed.text
    )

    archived = client.post(
        f"/api/conversations/{owner_conversation}/archive",
        json={"user_id": owner.id},
    )
    archived_list = client.get(
        f"/local/conversations/{owner_conversation}/resources",
        params={"user_id": owner.id},
    )
    archived_attach = client.post(
        f"/local/conversations/{owner_conversation}/resources",
        json={
            "user_id": owner.id,
            "resource_kind": "local_file",
            "source_ref": str(owner_file),
        },
    )
    assert archived.status_code == 200
    assert archived_list.status_code == 404
    assert archived_attach.status_code == 404


def test_task_submission_binds_ordered_versions_and_keeps_empty_compatibility(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Selected resources are de-duplicated and fixed into Task Context Snapshot."""
    owner = anyio.run(create_user, sessionmaker, "Task Resource Owner")
    conversation_id = create_conversation(client, owner.id, "Task Context")
    first_file = tmp_path / "first.txt"
    second_file = tmp_path / "second.txt"
    first_file.write_text("first", encoding="utf-8")
    second_file.write_text("second", encoding="utf-8")
    first = attach_file(
        client,
        conversation_id=conversation_id,
        user_id=owner.id,
        path=first_file,
    )
    second = attach_file(
        client,
        conversation_id=conversation_id,
        user_id=owner.id,
        path=second_file,
    )

    submitted = client.post(
        "/local/tasks",
        json={
            "user_id": owner.id,
            "task_type": "office",
            "input_text": "compare files",
            "conversation_id": conversation_id,
            "resource_reference_ids": [
                second["reference_id"],
                first["reference_id"],
                second["reference_id"],
            ],
        },
    )
    assert submitted.status_code == 201, submitted.text
    task_id = submitted.json()["task"]["task_id"]
    context = client.get(
        f"/local/tasks/{task_id}/context", params={"user_id": owner.id}
    )
    assert context.status_code == 200
    assert context.json()["resource_reference_ids"] == [
        second["reference_id"],
        first["reference_id"],
    ]
    assert context.json()["resolved_resource_versions"] == {
        str(second["reference_id"]): second["version"],
        str(first["reference_id"]): first["version"],
    }

    compatible = client.post(
        "/local/tasks",
        json={
            "user_id": owner.id,
            "task_type": "office",
            "input_text": "no files",
            "conversation_id": conversation_id,
        },
    )
    compatible_context = client.get(
        f"/local/tasks/{compatible.json()['task']['task_id']}/context",
        params={"user_id": owner.id},
    )
    assert compatible.status_code == 201
    assert compatible_context.json()["resource_reference_ids"] == []


@pytest.mark.asyncio
async def test_task_submission_rejects_foreign_removed_missing_and_changed_references(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Invalid references fail atomically before Task persistence or queueing."""
    owner = await create_user(sessionmaker, "Atomic Owner")
    other = await create_user(sessionmaker, "Atomic Other")
    owner_conversation = create_conversation(client, owner.id, "Atomic Owner")
    other_conversation = create_conversation(client, other.id, "Atomic Other")
    owner_file = tmp_path / "owner.txt"
    other_file = tmp_path / "other.txt"
    owner_file.write_text("owner-v1", encoding="utf-8")
    other_file.write_text("other-v1", encoding="utf-8")
    owner_ref = attach_file(
        client,
        conversation_id=owner_conversation,
        user_id=owner.id,
        path=owner_file,
    )
    other_ref = attach_file(
        client,
        conversation_id=other_conversation,
        user_id=other.id,
        path=other_file,
    )

    async with sessionmaker() as session:
        before = (
            int(await session.scalar(select(func.count()).select_from(Task)) or 0),
            int(
                await session.scalar(
                    select(func.count()).select_from(ConversationMessage)
                )
                or 0
            ),
            int(
                await session.scalar(
                    select(func.count()).select_from(TaskContextSnapshot)
                )
                or 0
            ),
        )

    def submit(reference_id: object) -> Response:
        """Submit one resource ID and return the TestClient response."""
        return client.post(
            "/local/tasks",
            json={
                "user_id": owner.id,
                "task_type": "office",
                "input_text": "must fail atomically",
                "conversation_id": owner_conversation,
                "resource_reference_ids": [reference_id],
            },
        )

    foreign = submit(other_ref["reference_id"])
    assert foreign.status_code == 404

    removed = client.delete(
        f"/local/conversations/{owner_conversation}/resources/{owner_ref['reference_id']}",
        params={"user_id": owner.id},
    )
    unavailable = submit(owner_ref["reference_id"])
    assert removed.status_code == 200
    assert unavailable.status_code == 409

    reattached = attach_file(
        client,
        conversation_id=owner_conversation,
        user_id=owner.id,
        path=owner_file,
    )
    owner_file.unlink()
    missing = submit(reattached["reference_id"])
    assert missing.status_code == 409

    owner_file.write_text("owner-v2", encoding="utf-8")
    refreshed = attach_file(
        client,
        conversation_id=owner_conversation,
        user_id=owner.id,
        path=owner_file,
    )
    owner_file.write_text("owner-v3-with-different-size", encoding="utf-8")
    changed = submit(refreshed["reference_id"])
    assert changed.status_code == 409

    async with sessionmaker() as session:
        after = (
            int(await session.scalar(select(func.count()).select_from(Task)) or 0),
            int(
                await session.scalar(
                    select(func.count()).select_from(ConversationMessage)
                )
                or 0
            ),
            int(
                await session.scalar(
                    select(func.count()).select_from(TaskContextSnapshot)
                )
                or 0
            ),
        )
    assert after == before


def test_resource_reference_migration_is_linear_and_tenant_scoped() -> None:
    """The additive migration follows Task Context Snapshot and enables tenant RLS."""
    migration = (
        Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/202608110002_conversation_resource_references.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "202608110002"' in migration
    assert 'down_revision: str | None = "202608110001"' in migration
    assert '"conversation_resource_references"' in migration
    assert "uq_conversation_resource_locator" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
