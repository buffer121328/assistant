from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
import json
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from application.lifecycle_hooks import (
    HookDefinition,
    HookMatcher,
    LifecycleHookRegistry,
)
from application.artifact_lifecycle import (
    ArtifactLifecycleService,
    ArtifactUnavailableError,
    ArtifactValidationError,
)
from domain.models import (
    ArtifactRecord,
    Base,
    Conversation,
    GovernanceAudit,
    Task,
    TaskContextSnapshot,
    User,
)
from infrastructure.settings.config import Settings
from tools.builtin.artifacts import ArtifactStore


def settings_without_env(**values: object) -> Settings:
    """Call Pydantic Settings' documented runtime constructor override safely."""
    factory = cast(Callable[..., Settings], Settings)
    return factory(_env_file=None, **values)


PNG_BYTES = b"\x89PNG\r\n\x1a\nregistered-png"
ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/artifacts.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


async def _create_task_scope(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[str, str, str, str]:
    async with sessionmaker() as session:
        owner = User(display_name="Artifact Owner")
        other = User(display_name="Other Owner")
        session.add_all([owner, other])
        await session.flush()
        conversation = Conversation(
            user_id=owner.id,
            title="Artifact conversation",
            channel="desktop",
        )
        session.add(conversation)
        await session.flush()
        task = Task(
            user_id=owner.id,
            owner_id=owner.id,
            platform="local",
            task_type="screen",
            input_text="create an artifact",
            status="running",
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
                organization_id=None,
                owner_type="user",
                owner_id=owner.id,
                visibility="private",
                state="finalized",
                resource_reference_ids_json='["resource-1"]',
                resolved_resource_versions_json='{"resource-1":"version-1"}',
                memory_scope_snapshot_json="[]",
                knowledge_scope_snapshot_json="[]",
                capability_snapshot_json="[]",
            )
        )
        await session.commit()
        return owner.id, other.id, conversation.id, task.id


async def _register(
    sessionmaker: async_sessionmaker[AsyncSession],
    root: Path,
    *,
    task_id: str,
    owner_id: str,
    idempotency_key: str = "capture-1",
):
    async with sessionmaker() as session:
        return await ArtifactLifecycleService(
            session,
            store=ArtifactStore(root),
        ).register_bytes(
            task_id=task_id,
            actor_user_id=owner_id,
            filename="screenshot.png",
            media_type="image/png",
            data=PNG_BYTES,
            generation_method="desktop.screenshot",
            idempotency_key=idempotency_key,
        )


@pytest.mark.asyncio
async def test_registered_artifact_publishes_a_sanitized_post_commit_hook_fact(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    owner_id, _other_id, _conversation_id, task_id = await _create_task_scope(
        sessionmaker
    )
    observed = []
    registry = LifecycleHookRegistry()

    async def observer(event):
        observed.append(event)
        return None

    registry.register(
        HookDefinition(
            name="test.artifact-hook",
            version="1",
            source_kind="builtin",
            source_id="tests",
            matcher=HookMatcher(frozenset({"artifact.registered"})),
            mode="observer",
            handler=observer,
        )
    )

    async with sessionmaker() as session:
        artifact = await ArtifactLifecycleService(
            session,
            store=ArtifactStore(tmp_path / "hook-artifacts"),
            lifecycle_registry=registry,
        ).register_bytes(
            task_id=task_id,
            actor_user_id=owner_id,
            filename="hook.png",
            media_type="image/png",
            data=PNG_BYTES,
            generation_method="desktop.screenshot",
            idempotency_key="artifact-hook-event",
        )

    assert [event.event_type for event in observed] == ["artifact.registered"]
    assert observed[0].payload["artifact_id"] == artifact.id
    assert "path" not in observed[0].payload


@pytest.mark.asyncio
async def test_registration_is_persistent_immutable_idempotent_and_path_free(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    owner_id, other_id, conversation_id, task_id = await _create_task_scope(
        sessionmaker
    )
    root = tmp_path / "managed-artifacts"

    first = await _register(
        sessionmaker,
        root,
        task_id=task_id,
        owner_id=owner_id,
    )
    second = await _register(
        sessionmaker,
        root,
        task_id=task_id,
        owner_id=owner_id,
    )

    assert first.id == second.id
    assert first.content_hash == second.content_hash
    assert first.conversation_id == conversation_id
    assert first.owner_id == owner_id
    assert first.task_id == task_id
    assert first.source_reference_versions == {"resource-1": "version-1"}
    assert first.content_hash
    assert first.media_type == "image/png"
    assert first.size_bytes == len(PNG_BYTES)
    assert first.version == "1"
    assert first.lifecycle_state == "registered"
    assert first.publication_state == "unpublished"
    assert first.retention_state == "retained"
    assert "storage_reference" not in asdict(first)
    assert "managed-artifacts" not in json.dumps(asdict(first), default=str)

    async with sessionmaker() as session:
        records = tuple(await session.scalars(select(ArtifactRecord)))
    assert len(records) == 1
    assert records[0].storage_reference.startswith(f"{task_id}/")
    assert records[0].storage_reference.endswith(".png")
    assert records[0].storage_reference != f"{task_id}/screenshot.png"
    assert (root / records[0].storage_reference).read_bytes() == PNG_BYTES

    async with sessionmaker() as session:
        service = ArtifactLifecycleService(session, store=ArtifactStore(root))
        with pytest.raises(ArtifactUnavailableError):
            await service.register_bytes(
                task_id=task_id,
                actor_user_id=other_id,
                filename="screenshot.png",
                media_type="image/png",
                data=PNG_BYTES,
                generation_method="desktop.screenshot",
                idempotency_key="cross-owner",
            )
        with pytest.raises(ArtifactValidationError):
            await service.register_bytes(
                task_id=task_id,
                actor_user_id=owner_id,
                filename="screenshot.png",
                media_type="image/png",
                data=PNG_BYTES,
                generation_method="desktop.screenshot",
                idempotency_key="unsupported-share",
                visibility="space",
            )


@pytest.mark.asyncio
async def test_owner_listing_archive_revoke_and_conversation_archive_are_independent(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    owner_id, other_id, conversation_id, task_id = await _create_task_scope(
        sessionmaker
    )
    root = tmp_path / "managed-artifacts"
    artifact = await _register(
        sessionmaker,
        root,
        task_id=task_id,
        owner_id=owner_id,
    )

    async with sessionmaker() as session:
        service = ArtifactLifecycleService(session, store=ArtifactStore(root))
        listed = await service.list_conversation(
            conversation_id=conversation_id,
            actor_user_id=owner_id,
        )
        assert [item.id for item in listed] == [artifact.id]
        with pytest.raises(ArtifactUnavailableError):
            await service.list_conversation(
                conversation_id=conversation_id,
                actor_user_id=other_id,
            )
        archived = await service.archive(
            artifact_id=artifact.id,
            actor_user_id=owner_id,
        )
        archived_again = await service.archive(
            artifact_id=artifact.id,
            actor_user_id=owner_id,
        )
        assert archived.lifecycle_state == "archived"
        assert archived_again.id == archived.id
        assert archived_again.lifecycle_state == archived.lifecycle_state
        assert archived.publication_state == "unpublished"

    async with sessionmaker() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        from domain.models import utc_now

        conversation.archived_at = utc_now()
        await session.commit()

    async with sessionmaker() as session:
        service = ArtifactLifecycleService(session, store=ArtifactStore(root))
        assert [
            item.id
            for item in await service.list_conversation(
                conversation_id=conversation_id,
                actor_user_id=owner_id,
            )
        ] == [artifact.id]
        revoked = await service.revoke(
            artifact_id=artifact.id,
            actor_user_id=owner_id,
        )
        revoked_again = await service.revoke(
            artifact_id=artifact.id,
            actor_user_id=owner_id,
        )
        assert revoked.lifecycle_state == "revoked"
        assert revoked_again.id == revoked.id
        assert revoked_again.lifecycle_state == revoked.lifecycle_state
        assert (
            await service.list_conversation(
                conversation_id=conversation_id,
                actor_user_id=owner_id,
            )
        ) == ()

    assert (
        root / (await _record(sessionmaker, artifact.id)).storage_reference
    ).exists()


async def _record(
    sessionmaker: async_sessionmaker[AsyncSession], artifact_id: str
) -> ArtifactRecord:
    async with sessionmaker() as session:
        record = await session.get(ArtifactRecord, artifact_id)
        assert record is not None
        return record


@pytest.mark.asyncio
async def test_download_reauthorizes_integrity_and_records_governance_audit(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    owner_id, other_id, _conversation_id, task_id = await _create_task_scope(
        sessionmaker
    )
    root = tmp_path / "managed-artifacts"
    artifact = await _register(
        sessionmaker,
        root,
        task_id=task_id,
        owner_id=owner_id,
    )

    async with sessionmaker() as session:
        download = await ArtifactLifecycleService(
            session, store=ArtifactStore(root)
        ).resolve_download(artifact_id=artifact.id, actor_user_id=owner_id)
    assert download.path.read_bytes() == PNG_BYTES
    assert download.filename == "screenshot.png"
    assert download.media_type == "image/png"

    async with sessionmaker() as session:
        with pytest.raises(ArtifactUnavailableError):
            await ArtifactLifecycleService(
                session, store=ArtifactStore(root)
            ).resolve_download(artifact_id=artifact.id, actor_user_id=other_id)

    record = await _record(sessionmaker, artifact.id)
    (root / record.storage_reference).write_bytes(b"changed")
    async with sessionmaker() as session:
        with pytest.raises(ArtifactUnavailableError):
            await ArtifactLifecycleService(
                session, store=ArtifactStore(root)
            ).resolve_download(artifact_id=artifact.id, actor_user_id=owner_id)

    async with sessionmaker() as session:
        audits = tuple(
            await session.scalars(
                select(GovernanceAudit)
                .where(GovernanceAudit.resource_id == artifact.id)
                .order_by(GovernanceAudit.created_at)
            )
        )
    assert [(row.policy_decision, row.result_status) for row in audits] == [
        ("ALLOW", "download_resolved"),
        ("DENY", "owner_scope_denied"),
        ("DENY", "integrity_failed"),
    ]
    assert all(row.resource_type == "artifact" for row in audits)
    assert all("managed-artifacts" not in row.summary for row in audits)


@pytest.mark.asyncio
async def test_local_artifact_api_lists_downloads_and_transitions_without_paths(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    owner_id, other_id, conversation_id, task_id = await _create_task_scope(
        sessionmaker
    )
    root = tmp_path / "managed-artifacts"
    artifact = await _register(
        sessionmaker,
        root,
        task_id=task_id,
        owner_id=owner_id,
    )
    settings = settings_without_env(
        database_url="sqlite+aiosqlite:///unused.db",
        redis_url="redis://placeholder",
        artifacts_root=root,
    )
    app = create_app(settings)
    app.state.db_sessionmaker = sessionmaker

    with TestClient(app) as client:
        listed = client.get(
            f"/local/conversations/{conversation_id}/artifacts",
            params={"user_id": owner_id},
        )
        denied_list = client.get(
            f"/local/conversations/{conversation_id}/artifacts",
            params={"user_id": other_id},
        )
        downloaded = client.get(
            f"/local/artifacts/{artifact.id}/download",
            params={"user_id": owner_id},
        )
        archived = client.post(
            f"/local/artifacts/{artifact.id}/archive",
            json={"user_id": owner_id},
        )
        revoked = client.post(
            f"/local/artifacts/{artifact.id}/revoke",
            json={"user_id": owner_id},
        )
        denied_download = client.get(
            f"/local/artifacts/{artifact.id}/download",
            params={"user_id": owner_id},
        )

    assert listed.status_code == 200
    assert listed.json()["items"][0]["artifact_id"] == artifact.id
    assert "storage_reference" not in json.dumps(listed.json())
    assert denied_list.status_code == 404
    assert downloaded.status_code == 200
    assert downloaded.content == PNG_BYTES
    assert downloaded.headers["content-type"] == "image/png"
    assert "screenshot.png" in downloaded.headers["content-disposition"]
    assert archived.json()["lifecycle_state"] == "archived"
    assert revoked.json()["lifecycle_state"] == "revoked"
    assert denied_download.status_code == 404


def test_governed_artifact_migration_is_linear_and_tenant_scoped() -> None:
    """The additive Artifact migration follows Resource References and enables RLS."""
    migration = (
        ROOT / "backend/migrations/versions/202608110003_governed_artifacts.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "202608110003"' in migration
    assert 'down_revision: str | None = "202608110002"' in migration
    assert '"artifacts"' in migration
    assert "uq_artifacts_task_idempotency" in migration
    assert "ix_governance_audit_conversation_resource" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
