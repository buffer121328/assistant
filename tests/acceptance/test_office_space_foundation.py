from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from application.session_context.conversations import ConversationService
from application.session_context.spaces import SpaceError, SpaceService
from application.task_execution.lifecycle import TaskService
from domain.models import (
    Base,
    Conversation,
    ConversationMessage,
    Organization,
    OrganizationMembership,
    Space,
    SpaceMembership,
    Task,
    TaskContextSnapshot,
    Tenant,
    User,
)
from infrastructure.settings.config import Settings


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create an isolated database for Space acceptance scenarios."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/spaces.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def client(sessionmaker: async_sessionmaker[AsyncSession]) -> TestClient:
    """Expose the internal API with the isolated acceptance database."""
    app = create_app(
        Settings(
            database_url="sqlite+aiosqlite:///unused.db",
            redis_url="redis://placeholder",
        )
    )
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


async def create_user(
    sessionmaker: async_sessionmaker[AsyncSession],
    name: str,
    *,
    tenant_id: str = "local",
) -> User:
    """Persist one user in a caller-selected tenant."""
    async with sessionmaker() as session:
        if await session.get(Tenant, tenant_id) is None:
            session.add(Tenant(id=tenant_id, name=f"Tenant {tenant_id}"))
            await session.flush()
        user = User(display_name=name, tenant_id=tenant_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.mark.asyncio
async def test_space_service_governs_lifecycle_membership_and_tenant_isolation(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Owners govern membership while tenant and role checks fail closed."""
    owner = await create_user(sessionmaker, "Owner")
    editor = await create_user(sessionmaker, "Editor")
    viewer = await create_user(sessionmaker, "Viewer")
    outsider = await create_user(sessionmaker, "Outsider", tenant_id="other")

    async with sessionmaker() as session:
        service = SpaceService(session)
        space = await service.create(user_id=owner.id, name="Quarterly planning")
        await service.add_member(
            space_id=space.id,
            actor_user_id=owner.id,
            member_user_id=editor.id,
            role="editor",
        )
        await service.add_member(
            space_id=space.id,
            actor_user_id=owner.id,
            member_user_id=viewer.id,
            role="viewer",
        )

        assert [item.id for item in await service.list_accessible(editor.id)] == [
            space.id
        ]
        assert (await service.require_edit_access(space.id, editor.id)).id == space.id
        with pytest.raises(SpaceError, match="space_edit_forbidden"):
            await service.require_edit_access(space.id, viewer.id)
        with pytest.raises(SpaceError, match="space_member_tenant_mismatch"):
            await service.add_member(
                space_id=space.id,
                actor_user_id=owner.id,
                member_user_id=outsider.id,
                role="editor",
            )
        with pytest.raises(SpaceError, match="space_owner_required"):
            await service.archive(space_id=space.id, user_id=editor.id)

        await service.revoke_member(
            space_id=space.id,
            actor_user_id=owner.id,
            member_user_id=editor.id,
        )
        assert await service.list_accessible(editor.id) == []
        with pytest.raises(SpaceError, match="space_owner_membership_immutable"):
            await service.revoke_member(
                space_id=space.id,
                actor_user_id=owner.id,
                member_user_id=owner.id,
            )

        archived = await service.archive(space_id=space.id, user_id=owner.id)
        assert archived.status == "archived"
        assert await service.list_accessible(owner.id) == []


@pytest.mark.asyncio
async def test_organization_scoped_space_requires_active_same_tenant_membership(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Organization scope is accepted only from persisted active membership."""
    owner = await create_user(sessionmaker, "Org owner")
    non_member = await create_user(sessionmaker, "Not in org")
    foreign = await create_user(sessionmaker, "Foreign", tenant_id="foreign")
    async with sessionmaker() as session:
        organization = Organization(
            tenant_id="local", name="Operations", status="active"
        )
        foreign_organization = Organization(
            tenant_id="foreign", name="Foreign org", status="active"
        )
        session.add_all([organization, foreign_organization])
        await session.flush()
        session.add(
            OrganizationMembership(
                tenant_id="local",
                organization_id=organization.id,
                user_id=owner.id,
                role="member",
                status="active",
            )
        )
        await session.commit()

        service = SpaceService(session)
        space = await service.create(
            user_id=owner.id,
            name="Operations space",
            organization_id=organization.id,
        )
        assert space.organization_id == organization.id
        with pytest.raises(SpaceError, match="space_organization_membership_required"):
            await service.create(
                user_id=non_member.id,
                name="Denied",
                organization_id=organization.id,
            )
        with pytest.raises(SpaceError, match="space_organization_not_found"):
            await service.create(
                user_id=foreign.id,
                name="Cross tenant",
                organization_id=organization.id,
            )


@pytest.mark.asyncio
async def test_space_binding_stays_private_and_task_scope_is_trusted(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Space binding copies trusted scope without sharing private history."""
    owner = await create_user(sessionmaker, "Owner")
    editor = await create_user(sessionmaker, "Editor")
    viewer = await create_user(sessionmaker, "Viewer")
    async with sessionmaker() as session:
        spaces = SpaceService(session)
        space = await spaces.create(user_id=owner.id, name="Shared planning")
        await spaces.add_member(
            space_id=space.id,
            actor_user_id=owner.id,
            member_user_id=editor.id,
            role="editor",
        )
        await spaces.add_member(
            space_id=space.id,
            actor_user_id=owner.id,
            member_user_id=viewer.id,
            role="viewer",
        )
        conversation = await ConversationService(session).create(
            user_id=editor.id,
            title="Private work",
            space_id=space.id,
        )

        assert conversation.tenant_id == editor.tenant_id
        assert conversation.organization_id == space.organization_id
        assert conversation.space_id == space.id
        assert conversation.owner_type == "user"
        assert conversation.owner_id == editor.id
        assert conversation.visibility == "private"

        with pytest.raises(SpaceError, match="space_edit_forbidden"):
            await ConversationService(session).create(
                user_id=viewer.id,
                title="Viewer denied",
                space_id=space.id,
            )
        with pytest.raises(Exception, match="conversation_not_found"):
            await ConversationService(session).get_owned(
                conversation_id=conversation.id,
                user_id=owner.id,
            )

        task = await TaskService(session).create_task(
            user_id=editor.id,
            platform="api",
            task_type="plan",
            input_text="Continue safely",
            conversation_id=conversation.id,
        )
        snapshot = await session.scalar(
            select(TaskContextSnapshot).where(TaskContextSnapshot.task_id == task.id)
        )
        assert (
            task.tenant_id,
            task.organization_id,
            task.owner_type,
            task.owner_id,
            task.visibility,
        ) == (
            conversation.tenant_id,
            conversation.organization_id,
            conversation.owner_type,
            conversation.owner_id,
            conversation.visibility,
        )
        assert snapshot is not None
        assert snapshot.tenant_id == conversation.tenant_id
        assert snapshot.owner_id == editor.id


@pytest.mark.asyncio
async def test_space_access_loss_rolls_back_task_message_and_snapshot(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Task submission writes nothing after archive or membership revocation."""
    owner = await create_user(sessionmaker, "Owner")
    editor = await create_user(sessionmaker, "Editor")
    async with sessionmaker() as session:
        spaces = SpaceService(session)
        space = await spaces.create(user_id=owner.id, name="Controlled")
        await spaces.add_member(
            space_id=space.id,
            actor_user_id=owner.id,
            member_user_id=editor.id,
            role="editor",
        )
        conversation = await ConversationService(session).create(
            user_id=editor.id, space_id=space.id
        )
        await spaces.revoke_member(
            space_id=space.id,
            actor_user_id=owner.id,
            member_user_id=editor.id,
        )

        with pytest.raises(Exception, match="conversation_not_found"):
            await TaskService(session).create_task(
                user_id=editor.id,
                platform="api",
                task_type="plan",
                input_text="Must not persist",
                conversation_id=conversation.id,
            )
        assert list(await session.scalars(select(Task))) == []
        assert list(await session.scalars(select(ConversationMessage))) == []
        assert list(await session.scalars(select(TaskContextSnapshot))) == []


def test_space_api_supports_membership_and_preserves_conversation_isolation(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Internal Space routes expose governed operations and safe errors."""
    import anyio

    owner = anyio.run(create_user, sessionmaker, "API owner")
    editor = anyio.run(create_user, sessionmaker, "API editor")

    created = client.post(
        "/api/spaces", json={"user_id": owner.id, "name": "API space", "description": None}
    )
    assert created.status_code == 201
    space_id = created.json()["space_id"]
    assert created.json()["role"] == "owner"
    assert created.json()["description"] == ""

    added = client.post(
        f"/api/spaces/{space_id}/members",
        json={
            "user_id": owner.id,
            "member_user_id": editor.id,
            "role": "editor",
        },
    )
    assert added.status_code == 201
    assert added.json()["role"] == "editor"
    listed = client.get("/api/spaces", params={"user_id": editor.id})
    detail = client.get(f"/api/spaces/{space_id}", params={"user_id": editor.id})
    assert [item["space_id"] for item in listed.json()["items"]] == [space_id]
    assert detail.status_code == 200
    assert {item["user_id"] for item in detail.json()["members"]} == {
        owner.id,
        editor.id,
    }

    conversation = client.post(
        "/api/conversations",
        json={"user_id": editor.id, "title": "Scoped", "space_id": space_id},
    )
    assert conversation.status_code == 201
    assert conversation.json()["space_id"] == space_id
    assert conversation.json()["owner_id"] == editor.id
    denied = client.get(
        f"/api/conversations/{conversation.json()['conversation_id']}/messages",
        params={"user_id": owner.id},
    )
    assert denied.status_code == 404

    revoked = client.post(
        f"/api/spaces/{space_id}/members/{editor.id}/revoke",
        json={"user_id": owner.id},
    )
    assert revoked.status_code == 200
    archived = client.post(
        f"/api/spaces/{space_id}/archive", json={"user_id": owner.id}
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"


def test_space_migration_is_linear_backfills_and_applies_tenant_policies() -> None:
    """The additive migration follows current head and preserves old conversations."""
    migration = (
        Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/202608110004_office_spaces.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "202608110004"' in migration
    assert 'down_revision: str | None = "202608110003"' in migration
    assert 'op.create_table(\n        "spaces"' in migration
    assert 'op.create_table(\n        "space_memberships"' in migration
    assert "UPDATE conversations" in migration
    assert "FROM users" in migration
    assert 'op.alter_column("conversations", "tenant_id", nullable=False)' in migration
    for table in ("spaces", "space_memberships", "conversations"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in migration
        assert f"CREATE POLICY app_tenant_isolation ON {table}" in migration


@pytest.mark.asyncio
async def test_existing_unbound_conversation_remains_private_and_usable(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A conversation without Space binding follows unchanged owner semantics."""
    owner = await create_user(
        sessionmaker, "Legacy owner", tenant_id="legacy-tenant"
    )
    async with sessionmaker() as session:
        conversation = await ConversationService(session).create(user_id=owner.id)
        task = await TaskService(session).create_task(
            user_id=owner.id,
            platform="api",
            task_type="status",
            input_text="Still works",
            conversation_id=conversation.id,
        )
        assert conversation.space_id is None
        assert task.conversation_id == conversation.id
        assert task.owner_id == owner.id
        assert task.tenant_id == owner.tenant_id
        assert task.organization_id is None
        assert await session.get(Conversation, conversation.id) is not None
        assert await session.scalar(select(SpaceMembership)) is None
        assert await session.scalar(select(Space)) is None

@pytest.mark.asyncio
async def test_owner_can_move_or_unbind_private_conversation_without_widening_space_access(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """An editor may organize only their own private Conversation and later return to Personal Work."""
    owner = await create_user(sessionmaker, "Space owner")
    editor = await create_user(sessionmaker, "Space editor")
    viewer = await create_user(sessionmaker, "Space viewer")

    async with sessionmaker() as session:
        spaces = SpaceService(session)
        conversations = ConversationService(session)
        space = await spaces.create(user_id=owner.id, name="Client A")
        await spaces.add_member(
            space_id=space.id,
            actor_user_id=owner.id,
            member_user_id=editor.id,
            role="editor",
        )
        await spaces.add_member(
            space_id=space.id,
            actor_user_id=owner.id,
            member_user_id=viewer.id,
            role="viewer",
        )

        conversation = await conversations.create(user_id=editor.id, title="Personal")
        assert conversation.space_id is None

        moved = await conversations.set_space(
            conversation_id=conversation.id,
            user_id=editor.id,
            space_id=space.id,
        )
        assert moved.space_id == space.id
        assert moved.owner_id == editor.id
        assert moved.visibility == "private"

        summary = await conversations.work_summary(
            conversation_id=conversation.id,
            user_id=editor.id,
        )
        assert summary.scope_kind == "space"
        assert summary.scope_name == "Client A"
        assert summary.context_count == 0
        assert summary.artifact_count == 0
        assert summary.pending_approval_count == 0

        unbound = await conversations.set_space(
            conversation_id=conversation.id,
            user_id=editor.id,
            space_id=None,
        )
        assert unbound.space_id is None
        personal_summary = await conversations.work_summary(
            conversation_id=conversation.id,
            user_id=editor.id,
        )
        assert personal_summary.scope_kind == "personal"
        assert personal_summary.scope_name == "Personal Work"

        viewer_conversation = await conversations.create(user_id=viewer.id, title="Viewer")
        with pytest.raises(SpaceError, match="space_edit_forbidden"):
            await conversations.set_space(
                conversation_id=viewer_conversation.id,
                user_id=viewer.id,
                space_id=space.id,
            )


@pytest.mark.asyncio
async def test_space_context_candidates_remain_owner_scoped_and_are_copied_before_snapshot(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A Space may suggest only the caller's already-authorized references and never leaks another owner."""
    from application.session_context.resource_references import ResourceReferenceService

    owner = await create_user(sessionmaker, "Context owner")
    member = await create_user(sessionmaker, "Context member")

    async with sessionmaker() as session:
        spaces = SpaceService(session)
        conversations = ConversationService(session)
        resources = ResourceReferenceService(session)
        space = await spaces.create(user_id=owner.id, name="Budget")
        await spaces.add_member(
            space_id=space.id,
            actor_user_id=owner.id,
            member_user_id=member.id,
            role="editor",
        )

        source = await conversations.create(
            user_id=member.id, title="Source", space_id=space.id
        )
        target = await conversations.create(
            user_id=member.id, title="Target", space_id=space.id
        )
        private_owner_conversation = await conversations.create(
            user_id=owner.id, title="Owner-only", space_id=space.id
        )
        member_reference = await resources.attach_external_record(
            conversation_id=source.id,
            user_id=member.id,
            provider="calendar",
            external_resource_id="meeting-42",
            display_name="Budget review",
        )
        await resources.attach_external_record(
            conversation_id=private_owner_conversation.id,
            user_id=owner.id,
            provider="calendar",
            external_resource_id="owner-only",
            display_name="Owner meeting",
        )

        candidates = await resources.list_space_context_candidates(
            conversation_id=target.id,
            user_id=member.id,
        )
        assert [item.id for item in candidates] == [member_reference.id]
        copied = await resources.attach_space_context_candidate(
            conversation_id=target.id,
            reference_id=member_reference.id,
            user_id=member.id,
        )
        assert copied.conversation_id == target.id
        assert copied.id != member_reference.id
        assert [item.id for item in await resources.list_attached(
            conversation_id=target.id, user_id=member.id
        )] == [copied.id]

def test_local_conversation_scope_routes_return_bounded_summary_and_safe_space_mutation(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Desktop local routes expose only owner-scoped scope data and enforce editor membership."""
    import anyio

    owner = anyio.run(create_user, sessionmaker, "Local owner")
    editor = anyio.run(create_user, sessionmaker, "Local editor")
    created_space = client.post(
        "/api/spaces", json={"user_id": owner.id, "name": "Local client"}
    )
    assert created_space.status_code == 201
    space_id = created_space.json()["space_id"]
    assert client.post(
        f"/api/spaces/{space_id}/members",
        json={"user_id": owner.id, "member_user_id": editor.id, "role": "editor"},
    ).status_code == 201
    created_conversation = client.post(
        "/api/conversations", json={"user_id": editor.id, "title": "Personal"}
    )
    assert created_conversation.status_code == 201
    conversation_id = created_conversation.json()["conversation_id"]

    personal = client.get(
        f"/local/conversations/{conversation_id}/work-summary",
        params={"user_id": editor.id},
    )
    assert personal.status_code == 200
    assert personal.json()["scope_kind"] == "personal"
    assert personal.json()["scope_name"] == "Personal Work"
    assert set(personal.json()) == {
        "conversation_id",
        "scope_kind",
        "scope_name",
        "space_id",
        "context_count",
        "artifact_count",
        "pending_approval_count",
    }

    moved = client.patch(
        f"/local/conversations/{conversation_id}/space",
        json={"user_id": editor.id, "space_id": space_id},
    )
    assert moved.status_code == 200
    assert moved.json()["scope_kind"] == "space"
    assert moved.json()["scope_name"] == "Local client"

    returned = client.patch(
        f"/local/conversations/{conversation_id}/space",
        json={"user_id": editor.id, "space_id": None},
    )
    assert returned.status_code == 200
    assert returned.json()["scope_kind"] == "personal"
