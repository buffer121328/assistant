from __future__ import annotations

from collections.abc import AsyncIterator
from io import BytesIO
import json
import os
from pathlib import Path
import zipfile

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from infrastructure.settings.config import Settings
from app.main import create_app
from domain.models import Base, SkillAuditLog, User
from agent import (
    InvalidSkillPackageError,
    ManagedSkillConflictError,
    ManagedSkillStore,
    SkillDefinition,
    SkillResourceError,
)
from agent.capabilities import CapabilityDisabledError, build_default_registry


ROOT = Path(__file__).parents[2]


def skill_package(
    *,
    name: str = "meeting-notes",
    extra_entries: dict[str, bytes] | None = None,
) -> bytes:
    manifest = {
        "schema_version": 1,
        "name": name,
        "display_name": "Meeting Notes",
        "summary": "Turn raw notes into structured minutes.",
        "version": "1.0.0",
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(
            "SKILL.md",
            "# Meeting Notes\n\nTurn raw notes into structured minutes.\n\n"
            "Group decisions and follow-up actions.",
        )
        for filename, content in (extra_entries or {}).items():
            archive.writestr(filename, content)
    return buffer.getvalue()


def test_skill_loader_strips_frontmatter_from_builtin_instructions(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "portable-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: Portable Skill\n"
        "description: Metadata should stay out of runtime instructions.\n"
        "---\n\n"
        "Follow the detailed body instructions.",
        encoding="utf-8",
    )

    resolved = build_default_registry(skills_root).resolve("skill.portable-skill")

    assert isinstance(resolved, SkillDefinition)
    assert resolved.name == "portable-skill"
    assert resolved.instructions == "Follow the detailed body instructions."
    assert "---" not in resolved.instructions
    assert "description:" not in resolved.instructions


def test_managed_store_load_strips_frontmatter_from_package_instructions(
    tmp_path: Path,
) -> None:
    manifest = {
        "schema_version": 1,
        "name": "portable-skill",
        "display_name": "Portable Skill",
        "summary": "Metadata should stay out of runtime instructions.",
        "version": "1.0.0",
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(
            "SKILL.md",
            "---\n"
            "name: Portable Skill\n"
            "description: Metadata should stay out of runtime instructions.\n"
            "---\n\n"
            "Follow the managed body instructions.",
        )
    store = ManagedSkillStore(
        builtin_root=ROOT / "backend" / "resources" / "skillpacks",
        managed_root=tmp_path / "managed",
    )

    store.install(buffer.getvalue())
    loaded = store.load("portable-skill")

    assert loaded.instructions == "Follow the managed body instructions."
    assert "---" not in loaded.instructions
    assert "description:" not in loaded.instructions


def test_skill_loader_reads_builtin_resources_lazily_and_safely(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "resource-skill"
    templates_dir = skill_dir / "templates"
    templates_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Resource Skill\n\nUse templates only when requested.\n\n"
        "Follow the body instructions.",
        encoding="utf-8",
    )
    (templates_dir / "brief.md").write_text(
        "# Brief Template\n\nUse concise bullets.",
        encoding="utf-8",
    )
    (skill_dir / "too-large.txt").write_text("x" * (64 * 1024 + 1), encoding="utf-8")
    (skill_dir / "invalid.txt").write_bytes(b"\xff")
    (skill_dir / "escape").symlink_to(tmp_path / "outside.txt")
    (tmp_path / "outside.txt").write_text("must not read", encoding="utf-8")

    resolved = build_default_registry(skills_root).resolve("skill.resource-skill")

    assert isinstance(resolved, SkillDefinition)
    assert "Brief Template" not in resolved.instructions
    assert resolved.resource("templates/brief.md") == (
        "# Brief Template\n\nUse concise bullets."
    )
    for unsafe in (
        "../outside.txt",
        str(tmp_path / "outside.txt"),
        "missing.txt",
        "templates",
        "too-large.txt",
        "invalid.txt",
        "escape",
    ):
        with pytest.raises(SkillResourceError):
            resolved.resource(unsafe)


def test_managed_store_installs_readonly_resources_and_keeps_legacy_packages(
    tmp_path: Path,
) -> None:
    store = ManagedSkillStore(
        builtin_root=ROOT / "backend" / "resources" / "skillpacks",
        managed_root=tmp_path / "managed",
    )

    installed = store.install(
        skill_package(
            name="resource-skill",
            extra_entries={
                "templates/brief.md": b"# Brief Template\n\nUse concise bullets.",
                "data/example.txt": b"example data",
            },
        )
    )
    loaded = store.load("resource-skill")
    legacy = store.install(skill_package(name="legacy-skill"))

    assert installed.enabled is False
    assert loaded.resource("templates/brief.md") == (
        "# Brief Template\n\nUse concise bullets."
    )
    assert loaded.resource("data/example.txt") == "example data"
    assert legacy.name == "legacy-skill"
    with pytest.raises(SkillResourceError):
        loaded.resource("scripts/run.py")


def test_managed_store_rejects_unsafe_resource_packages_atomically(
    tmp_path: Path,
) -> None:
    store = ManagedSkillStore(
        builtin_root=ROOT / "backend" / "resources" / "skillpacks",
        managed_root=tmp_path / "managed",
    )

    for name, entries in {
        "scripted-skill": {"scripts/run.py": b"print('no')"},
        "dependency-skill": {"requirements.txt": b"requests"},
        "binary-skill": {"templates/bad.txt": b"\xff"},
        "large-skill": {"templates/large.txt": b"x" * (64 * 1024 + 1)},
    }.items():
        with pytest.raises(InvalidSkillPackageError):
            store.install(skill_package(name=name, extra_entries=entries))
        assert not (tmp_path / "managed" / name).exists()


def test_managed_store_creates_disabled_skill_and_controls_lifecycle(
    tmp_path: Path,
) -> None:
    store = ManagedSkillStore(
        builtin_root=ROOT / "backend" / "resources" / "skillpacks",
        managed_root=tmp_path / "managed",
    )

    created = store.create(
        name="meeting-notes",
        display_name="Meeting Notes",
        summary="Turn raw notes into structured minutes.",
        instructions="Group decisions and follow-up actions.",
    )

    assert created.enabled is False
    assert created.source == "managed"
    assert created.version == "1.0.0"
    assert {path.name for path in created.directory.iterdir()} == {
        "manifest.json",
        "SKILL.md",
    }
    with pytest.raises(CapabilityDisabledError):
        build_default_registry(
            ROOT / "backend" / "resources" / "skillpacks", managed_store=store
        ).resolve("skill.meeting-notes")

    enabled = store.set_enabled("meeting-notes", enabled=True)
    registry = build_default_registry(
        ROOT / "backend" / "resources" / "skillpacks", managed_store=store
    )
    resolved = registry.resolve("skill.meeting-notes")

    assert enabled.enabled is True
    assert isinstance(resolved, SkillDefinition)
    assert resolved.name == "meeting-notes"
    assert "follow-up actions" in resolved.instructions
    assert registry.resolve("skill.meeting-notes") is resolved

    store.set_enabled("meeting-notes", enabled=False)
    store.uninstall("meeting-notes")
    assert store.list_managed() == ()
    assert not (tmp_path / "managed" / "meeting-notes").exists()


def test_managed_store_rejects_unsafe_package_and_builtin_collision_atomically(
    tmp_path: Path,
) -> None:
    store = ManagedSkillStore(
        builtin_root=ROOT / "backend" / "resources" / "skillpacks",
        managed_root=tmp_path / "managed",
    )

    with pytest.raises(InvalidSkillPackageError):
        store.install(skill_package(extra_entries={"../escape": b"unsafe"}))
    with pytest.raises(ManagedSkillConflictError):
        store.create(
            name="research",
            display_name="Override Research",
            summary="Must not replace a bundled Skill.",
            instructions="Unsafe override attempt.",
        )

    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "managed" / "meeting-notes").exists()
    assert not (tmp_path / "managed" / "research").exists()


def test_managed_store_installs_exact_package_disabled(tmp_path: Path) -> None:
    store = ManagedSkillStore(
        builtin_root=ROOT / "backend" / "resources" / "skillpacks",
        managed_root=tmp_path / "managed",
    )

    installed = store.install(skill_package())

    assert installed.name == "meeting-notes"
    assert installed.enabled is False
    manifest = json.loads((installed.directory / "manifest.json").read_text())
    assert manifest == {
        "schema_version": 1,
        "name": "meeting-notes",
        "display_name": "Meeting Notes",
        "summary": "Turn raw notes into structured minutes.",
        "version": "1.0.0",
        "enabled": False,
    }


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v3-skills.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_user(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
    async with sessionmaker() as session:
        user = User(display_name="Skill Owner")
        session.add(user)
        await session.commit()
        return user.id


def lifecycle_client(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    managed_root: Path,
) -> TestClient:
    app = create_app(
        Settings(
            database_url="sqlite+aiosqlite:///unused.db",
            managed_skills_root=managed_root,
        )
    )
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


@pytest.mark.asyncio
async def test_skill_api_audits_mutations_and_refreshes_catalog(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    user_id = await create_user(sessionmaker)
    managed_root = tmp_path / "api-skills"

    with lifecycle_client(
        sessionmaker=sessionmaker, managed_root=managed_root
    ) as client:
        created = client.post(
            "/api/skills",
            json={
                "user_id": user_id,
                "name": "meeting-notes",
                "display_name": "Meeting Notes",
                "summary": "Turn raw notes into structured minutes.",
                "instructions": "Group decisions and follow-up actions.",
            },
        )
        enabled = client.post(
            "/api/skills/meeting-notes/enable",
            json={"user_id": user_id},
        )
        catalog = client.get(
            "/api/capabilities", params={"kind": "skill", "enabled": "true"}
        )
        conflict = client.post(
            "/api/skills",
            json={
                "user_id": user_id,
                "name": "meeting-notes",
                "display_name": "Duplicate",
                "summary": "Duplicate request.",
                "instructions": "Must fail safely.",
            },
        )
        listed = client.get("/api/skills")

    assert created.status_code == 201
    assert created.json()["enabled"] is False
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert conflict.status_code == 409
    assert "skill.meeting-notes" in {item["id"] for item in catalog.json()["items"]}
    meeting_notes = next(
        item for item in listed.json()["items"] if item["name"] == "meeting-notes"
    )
    assert meeting_notes == {
        "name": "meeting-notes",
        "display_name": "Meeting Notes",
        "summary": "Turn raw notes into structured minutes.",
        "version": "1.0.0",
        "source": "managed",
        "enabled": True,
        "manageable": True,
    }

    async with sessionmaker() as session:
        audits = list(
            await session.scalars(
                select(SkillAuditLog).order_by(SkillAuditLog.created_at)
            )
        )
    assert [(audit.action, audit.status, audit.error_code) for audit in audits] == [
        ("create", "succeeded", None),
        ("enable", "succeeded", None),
        ("create", "failed", "skill_conflict"),
    ]
    assert all(audit.actor_user_id == user_id for audit in audits)
    serialized = json.dumps(
        [
            {
                "name": audit.skill_name,
                "action": audit.action,
                "status": audit.status,
                "version": audit.version,
                "error": audit.error_code,
            }
            for audit in audits
        ]
    )
    assert "follow-up actions" not in serialized
    assert str(managed_root) not in serialized


@pytest.mark.asyncio
async def test_skill_api_installs_and_rejects_unknown_actor_without_mutation(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    user_id = await create_user(sessionmaker)
    managed_root = tmp_path / "install-skills"

    with lifecycle_client(
        sessionmaker=sessionmaker, managed_root=managed_root
    ) as client:
        installed = client.post(
            "/api/skills/install",
            data={"user_id": user_id},
            files={
                "package": ("meeting-notes.zip", skill_package(), "application/zip")
            },
        )
        unknown = client.post(
            "/api/skills/private-skill/enable",
            json={"user_id": "missing-user"},
        )
        removed = client.delete(
            "/api/skills/meeting-notes", params={"user_id": user_id}
        )

    assert installed.status_code == 201
    assert installed.json()["enabled"] is False
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "user_not_found"
    assert removed.status_code == 204
    assert not (managed_root / "meeting-notes").exists()

    async with sessionmaker() as session:
        audits = list(await session.scalars(select(SkillAuditLog)))
    assert {(audit.action, audit.status) for audit in audits} == {
        ("install", "succeeded"),
        ("uninstall", "succeeded"),
    }


def test_desktop_skill_client_contract(tmp_path: Path) -> None:
    from assistant_desktop.client import DesktopApiClient

    package_path = tmp_path / "meeting-notes.zip"
    package_path.write_bytes(skill_package())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path == "/api/skills":
            if request.method == "GET":
                return httpx.Response(200, json={"items": []})
            return httpx.Response(
                201,
                json={"name": "created", "enabled": False},
            )
        return httpx.Response(
            200 if request.url.path.endswith(("/enable", "/disable")) else 201,
            json={"name": "meeting-notes", "enabled": True},
        )

    client = DesktopApiClient(
        base_url="http://127.0.0.1:8000",
        user_id="user-1",
        transport=httpx.MockTransport(handler),
    )
    assert client.list_skills() == []
    client.create_skill(
        name="created",
        display_name="Created",
        summary="Created from the GUI.",
        instructions="Use a deterministic template.",
    )
    client.install_skill(package_path)
    client.set_skill_enabled("meeting-notes", enabled=True)
    client.set_skill_enabled("meeting-notes", enabled=False)
    client.uninstall_skill("meeting-notes")
    client.close()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/skills"),
        ("POST", "/api/skills"),
        ("POST", "/api/skills/install"),
        ("POST", "/api/skills/meeting-notes/enable"),
        ("POST", "/api/skills/meeting-notes/disable"),
        ("DELETE", "/api/skills/meeting-notes"),
    ]
    assert requests[-1].url.params["user_id"] == "user-1"
    assert b"user-1" in requests[2].content


def test_native_skill_dialog_has_safe_lifecycle_controls(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtCore")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QWidget

    from assistant_desktop.skill_dialog import SkillManagerDialog
    from assistant_desktop.window import TaskWindow

    application = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "desktop.ini"), QSettings.Format.IniFormat)
    settings.setValue("api_base_url", "http://127.0.0.1:8000")
    settings.setValue("user_id", "user-1")
    window = TaskWindow(settings=settings)
    dialog = SkillManagerDialog(
        base_url="http://127.0.0.1:8000",
        user_id="user-1",
        parent=window,
    )

    assert window.findChild(QWidget, "manage_skills") is not None
    for object_name in (
        "skill_list",
        "skill_name",
        "skill_display_name",
        "skill_summary",
        "skill_instructions",
        "create_skill",
        "install_skill",
        "enable_skill",
        "disable_skill",
        "uninstall_skill",
    ):
        assert dialog.findChild(QWidget, object_name) is not None

    dialog.close()
    window.shutdown()
    application.processEvents()
