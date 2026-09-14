from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from application.artifact_lifecycle import ArtifactLifecycleService
from application.session_context.conversations import ConversationService
from application.session_context.resource_references import ResourceReferenceService
from application.session_context.spaces import SpaceError, SpaceService
from application.session_context.workspace_files import WorkspaceFileService
from application.task_execution.lifecycle import TaskService
from domain.models import Base, Tenant, User
from tools.builtin.artifacts import ArtifactStore


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/workspace-files.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _user(
    sessionmaker: async_sessionmaker[AsyncSession], name: str
) -> User:
    async with sessionmaker() as session:
        if await session.get(Tenant, "local") is None:
            session.add(Tenant(id="local", name="Local"))
            await session.flush()
        item = User(display_name=name, tenant_id="local")
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item


async def _workspace_conversation(
    sessionmaker: async_sessionmaker[AsyncSession], *, user_id: str, name: str
) -> tuple[str, str]:
    async with sessionmaker() as session:
        space = await SpaceService(session).create(user_id=user_id, name=name)
        conversation = await ConversationService(session).create(
            user_id=user_id,
            title=f"{name} 对话",
            space_id=space.id,
        )
        return space.id, conversation.id


@pytest.mark.asyncio
async def test_private_workspace_lists_own_uploads_and_generated_files(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    owner = await _user(sessionmaker, "Owner")
    workspace_id, conversation_id = await _workspace_conversation(
        sessionmaker, user_id=owner.id, name="研究"
    )
    upload_root = tmp_path / "uploads"
    artifact_root = tmp_path / "artifacts"

    async with sessionmaker() as session:
        uploaded = await ResourceReferenceService(
            session,
            uploaded_resources_root=upload_root,
            artifacts_root=artifact_root,
        ).attach_uploaded_file(
            conversation_id=conversation_id,
            user_id=owner.id,
            filename="资料.txt",
            content=b"workspace input",
        )
        task = await TaskService(session).create_task(
            user_id=owner.id,
            platform="local",
            task_type="plan",
            input_text="生成总结",
            conversation_id=conversation_id,
        )
        generated = await ArtifactLifecycleService(
            session,
            store=ArtifactStore(artifact_root),
        ).register_bytes(
            task_id=task.id,
            actor_user_id=owner.id,
            filename="总结.txt",
            media_type="text/plain",
            data=b"workspace output",
            generation_method="test",
            idempotency_key="workspace-summary",
        )

    async with sessionmaker() as session:
        files = await WorkspaceFileService(
            session,
            uploaded_resources_root=upload_root,
            artifacts_root=artifact_root,
        ).list_owned_workspace(
            workspace_id=workspace_id,
            user_id=owner.id,
        )
        assert {(item.id, item.source_kind) for item in files} == {
            (uploaded.id, "uploaded"),
            (generated.id, "generated"),
        }
        assert {item.conversation_id for item in files} == {conversation_id}


@pytest.mark.asyncio
async def test_workspace_files_follow_explicit_conversation_move_and_remain_owner_only(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    owner = await _user(sessionmaker, "Owner")
    other = await _user(sessionmaker, "Other")
    first_space_id, conversation_id = await _workspace_conversation(
        sessionmaker, user_id=owner.id, name="原工作区"
    )
    second_space_id, _ = await _workspace_conversation(
        sessionmaker, user_id=owner.id, name="新工作区"
    )
    upload_root = tmp_path / "uploads"

    async with sessionmaker() as session:
        await ResourceReferenceService(
            session,
            uploaded_resources_root=upload_root,
        ).attach_uploaded_file(
            conversation_id=conversation_id,
            user_id=owner.id,
            filename="移动资料.txt",
            content=b"move me",
        )
        assert len(
            await WorkspaceFileService(
                session,
                uploaded_resources_root=upload_root,
            ).list_owned_workspace(
                workspace_id=first_space_id,
                user_id=owner.id,
            )
        ) == 1
        await ConversationService(session).set_space(
            conversation_id=conversation_id,
            user_id=owner.id,
            space_id=second_space_id,
        )
        assert await WorkspaceFileService(
            session,
            uploaded_resources_root=upload_root,
        ).list_owned_workspace(
            workspace_id=first_space_id,
            user_id=owner.id,
        ) == ()
        assert len(
            await WorkspaceFileService(
                session,
                uploaded_resources_root=upload_root,
            ).list_owned_workspace(
                workspace_id=second_space_id,
                user_id=owner.id,
            )
        ) == 1
        with pytest.raises(SpaceError, match="space_not_found"):
            await WorkspaceFileService(
                session,
                uploaded_resources_root=upload_root,
            ).list_owned_workspace(
                workspace_id=second_space_id,
                user_id=other.id,
            )


def _client_for(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    uploaded_resources_root: Path | None = None,
    artifacts_root: Path | None = None,
):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from infrastructure.settings.config import Settings

    app = create_app(
        Settings(
            database_url="sqlite+aiosqlite:///unused.db",
            redis_url="redis://placeholder",
            resource_uploads_root=uploaded_resources_root or Path("var/resource-inputs"),
            artifacts_root=artifacts_root or Path("var/artifacts"),
        )
    )
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


@pytest.mark.asyncio
async def test_new_local_conversation_requires_and_uses_owned_workspace(
    sessionmaker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await _user(sessionmaker, "Owner")
    other = await _user(sessionmaker, "Other")
    workspace_id, _ = await _workspace_conversation(
        sessionmaker, user_id=owner.id, name="我的工作区"
    )
    other_workspace_id, _ = await _workspace_conversation(
        sessionmaker, user_id=other.id, name="其他工作区"
    )
    monkeypatch.setattr(
        "channels.desktop.local.tasks.safe_enqueue_task_execution",
        lambda *_args, **_kwargs: False,
    )

    with _client_for(sessionmaker) as client:
        missing = client.post(
            "/local/tasks",
            json={
                "user_id": owner.id,
                "task_type": "plan",
                "input_text": "没有工作区",
            },
        )
        assert missing.status_code == 422
        assert missing.json()["error"]["code"] == "workspace_required"

        foreign = client.post(
            "/local/tasks",
            json={
                "user_id": owner.id,
                "task_type": "plan",
                "input_text": "不能写入别人的工作区",
                "space_id": other_workspace_id,
            },
        )
        assert foreign.status_code == 404
        assert foreign.json()["error"]["code"] == "space_not_found"

        created = client.post(
            "/local/tasks",
            json={
                "user_id": owner.id,
                "task_type": "plan",
                "input_text": "归入当前工作区",
                "space_id": workspace_id,
            },
        )
        assert created.status_code == 201
        conversation_id = created.json()["task"]["conversation_id"]

    async with sessionmaker() as session:
        from domain.models import Conversation

        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.space_id == workspace_id
        assert conversation.user_id == owner.id


@pytest.mark.asyncio
async def test_local_workspace_file_routes_list_and_download_owned_uploads(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    owner = await _user(sessionmaker, "Owner")
    workspace_id, conversation_id = await _workspace_conversation(
        sessionmaker, user_id=owner.id, name="资料工作区"
    )
    upload_root = tmp_path / "uploads"
    source = tmp_path / "会议纪要.txt"
    source.write_bytes(b"meeting notes")

    with _client_for(sessionmaker, uploaded_resources_root=upload_root) as client:
        imported = client.post(
            f"/local/conversations/{conversation_id}/resources/import",
            json={
                "user_id": owner.id,
                "resource_kind": "local_file",
                "source_ref": str(source),
                "sensitivity": "normal",
                "pinned": False,
            },
        )
        assert imported.status_code == 201
        uploaded = imported.json()
        files = client.get(
            f"/local/workspaces/{workspace_id}/files",
            params={"user_id": owner.id},
        )
        assert files.status_code == 200
        assert files.json()["items"] == [
            {
                "file_id": uploaded["reference_id"],
                "source_kind": "uploaded",
                "display_name": "会议纪要.txt",
                "media_type": None,
                "size_bytes": len(b"meeting notes"),
                "conversation_id": conversation_id,
                "conversation_title": "资料工作区 对话",
                "created_at": files.json()["items"][0]["created_at"],
                "updated_at": files.json()["items"][0]["updated_at"],
            }
        ]
        download = client.get(
            f"/local/workspace-files/{uploaded['reference_id']}/download",
            params={"user_id": owner.id},
        )
        assert download.status_code == 200
        assert download.content == b"meeting notes"
