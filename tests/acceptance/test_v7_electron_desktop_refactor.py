from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import subprocess
import sys
import tomllib
import json

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from assistant_api.config import Settings
from assistant_api.main import create_app
from assistant_api.models import (
    Approval,
    ApprovalStatus,
    Base,
    Task,
    TaskStatus,
    User,
)
from assistant_api.task_events import TaskEventRepository


ROOT = Path(__file__).resolve().parents[2]
OPTIONAL_RUNTIME_DEPENDENCIES = {
    "PySide6",
    "playwright",
    "openpyxl",
    "pypdf",
    "python-docx",
    "python-pptx",
    "langfuse",
    "prometheus-client",
    "sentry-sdk",
}


@pytest_asyncio.fixture
async def db_sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


async def create_user(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
    async with sessionmaker() as session:
        user = User(display_name="V7 User")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


def create_test_client(sessionmaker: async_sessionmaker[AsyncSession]) -> TestClient:
    app = create_app(Settings(redis_url="redis://placeholder"))
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


def test_v7_optional_capabilities_are_not_main_runtime_dependencies() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    main_dependencies = "\n".join(pyproject["project"]["dependencies"]).lower()
    optional_dependencies = pyproject["project"]["optional-dependencies"]

    for dependency in OPTIONAL_RUNTIME_DEPENDENCIES:
        assert dependency.lower() not in main_dependencies
    assert "keyring" not in main_dependencies

    assert "desktop-pyside" not in optional_dependencies
    assert "browser-automation" in optional_dependencies
    assert "office" in optional_dependencies
    assert "observability" in optional_dependencies
    assert "pyside6" not in "\n".join(
        dependency for items in optional_dependencies.values() for dependency in items
    ).lower()
    assert any("playwright" in item.lower() for item in optional_dependencies["browser-automation"])
    assert any("python-docx" in item.lower() for item in optional_dependencies["office"])


def test_desktop_web_project_declares_electron_vite_react_shell() -> None:
    desktop_root = ROOT / "apps" / "desktop-web"
    package_json = tomllib.loads(
        (desktop_root / "package.toml").read_text()
    ) if (desktop_root / "package.toml").exists() else None
    package_text = (desktop_root / "package.json").read_text()
    main_source = (desktop_root / "src" / "main" / "index.ts").read_text()
    preload_source = (desktop_root / "src" / "preload" / "index.ts").read_text()

    assert package_json is None
    for expected in (
        '"dev"',
        '"build"',
        '"electron"',
        '"@vitejs/plugin-react"',
        '"vite"',
        '"react"',
        '"typescript"',
    ):
        assert expected in package_text
    assert "new BrowserWindow" in main_source
    assert "contextIsolation: true" in main_source
    assert "nodeIntegration: false" in main_source
    assert "sandbox: true" in main_source
    assert "setWindowOpenHandler" in main_source
    assert "shell.openExternal" in main_source
    assert "contextBridge.exposeInMainWorld" in preload_source
    assert "assistantDesktop" in preload_source
    assert "ipcRenderer" not in (desktop_root / "src" / "renderer" / "App.tsx").read_text()


def test_desktop_web_renderer_covers_task_console_and_developer_workflow() -> None:
    renderer_root = ROOT / "apps" / "desktop-web" / "src" / "renderer"
    app_source = (renderer_root / "App.tsx").read_text()
    api_source = (renderer_root / "api.ts").read_text()
    styles = (renderer_root / "styles.css").read_text()

    for endpoint in (
        "/local/health",
        "/local/config",
        "/local/tasks",
        "/events/stream",
        "/approvals/",
        "/settings/validate",
    ):
        assert endpoint in api_source
    for ui_token in (
        "task-list",
        "thread-panel",
        "logs-panel",
        "approval-panel",
        "diff-panel",
        "settings-panel",
        "risk-level",
        "command-output",
    ):
        assert ui_token in app_source or ui_token in styles
    for event_type in (
        "task.message.delta",
        "task.log.appended",
        "task.tool.requested",
        "task.failed",
        "task.completed",
    ):
        assert event_type in app_source
    assert "dangerouslySetInnerHTML" not in app_source


def test_desktop_web_renderer_polishes_console_information_architecture() -> None:
    renderer_root = ROOT / "apps" / "desktop-web" / "src" / "renderer"
    app_source = (renderer_root / "App.tsx").read_text()
    styles = (renderer_root / "styles.css").read_text()

    for ui_token in (
        "app-title-block",
        "task-overview",
        "metric-card",
        "thread-meta",
        "panel-summary",
        "approval-actions",
        "settings-actions",
        "task-empty",
        "inspector-empty",
        "primary-action",
        "secondary-action",
        "status-dot",
    ):
        assert ui_token in app_source or ui_token in styles

    for copy_token in (
        "Local task console",
        "Tasks",
        "Approvals",
        "Events",
        "Changes",
        "No tasks yet",
        "No pending approvals",
        "Validate settings",
    ):
        assert copy_token in app_source

    assert "approvalCount" in app_source
    assert "derivedItems.length" in app_source
    assert "selectedEvents.length" in app_source
    assert "linear-gradient" not in styles


def test_current_environment_docs_match_electron_desktop_boundary() -> None:
    startup_doc = (ROOT / "docs" / "mvp-startup-config.md").read_text(encoding="utf-8")

    for required in (
        "Python 3.12",
        "uv sync",
        "cp .env.example .env",
        "uv sync --extra browser-automation",
        "uv sync --extra office",
        "uv sync --extra observability",
        "cd apps/desktop-web",
        "npm ci",
        "npm run dev",
        "desktop-settings.json",
    ):
        assert required in startup_doc

    for retired in (
        "desktop-pyside",
        "pyside6",
        "PySide6",
        "PyQt",
        "assistant-desktop",
        "keyring",
        "QSettings",
    ):
        assert retired not in startup_doc


def test_desktop_web_release_configuration_excludes_runtime_bloat_and_documents_mode() -> None:
    desktop_root = ROOT / "apps" / "desktop-web"
    package_text = (desktop_root / "package.json").read_text()
    builder_config = json.loads((desktop_root / "electron-builder.json").read_text())
    release_notes = (desktop_root / "RELEASE.md").read_text()
    smoke_script = ROOT / "scripts" / "ops" / "desktop_web_release_check.py"

    assert (desktop_root / "package-lock.json").is_file()
    assert '"dist"' in package_text
    assert '"electron-builder"' in package_text
    assert builder_config["appId"] == "local.assistant.desktop"
    assert builder_config["directories"]["output"] == "release"
    assert "dist/**" in builder_config["files"]
    forbidden_patterns = "\n".join(builder_config["files"])
    for forbidden in (
        "!**/.venv/**",
        "!**/.git/**",
        "!**/.mypy_cache/**",
        "!**/.pytest_cache/**",
        "!**/.ruff_cache/**",
        "!**/tests/**",
        "!**/node_modules/.cache/**",
    ):
        assert forbidden in forbidden_patterns
    assert "external installed mode" in release_notes
    assert "not measured" in release_notes
    assert smoke_script.is_file()


def test_core_app_import_does_not_import_optional_gui_browser_or_office_modules() -> None:
    script = """
import importlib.abc
import sys

blocked = {"PySide6", "playwright", "docx", "openpyxl", "pypdf", "pptx", "langfuse"}

class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in blocked:
            raise AssertionError(f"optional dependency imported during core startup: {fullname}")
        return None

sys.meta_path.insert(0, BlockOptional())
sys.path.insert(0, "apps/api")
sys.path.insert(0, ".")

from assistant_api.main import create_app

create_app()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_local_api_creates_task_and_returns_electron_safe_snapshot(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(db_sessionmaker)

    with create_test_client(db_sessionmaker) as client:
        health = client.get("/local/health")
        config = client.get("/local/config")
        created = client.post(
            "/local/tasks",
            json={
                "user_id": user_id,
                "task_type": "plan",
                "input_text": "prepare an implementation plan",
            },
        )
        task_id = created.json()["task"]["task_id"]
        fetched = client.get(
            f"/local/tasks/{task_id}",
            params={"user_id": user_id},
        )

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert config.status_code == 200
    assert "token" not in str(config.json()).lower()
    assert created.status_code == 201
    assert isinstance(created.json()["queued"], bool)
    assert created.json()["task"]["platform"] == "local"
    assert fetched.status_code == 200
    assert fetched.json()["task_id"] == task_id
    assert fetched.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_local_api_returns_ordered_events_with_event_id_cursor(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(db_sessionmaker)
    async with db_sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="local",
            task_type="plan",
            input_text="stream this",
            status=TaskStatus.RUNNING.value,
        )
        session.add(task)
        await session.flush()
        first = await TaskEventRepository(session).append(
            task_id=task.id,
            user_id=user_id,
            event_type="task.started",
            payload={"message": "started"},
        )
        await TaskEventRepository(session).append(
            task_id=task.id,
            user_id=user_id,
            event_type="task.log.appended",
            payload={"message": "Bearer secret-token", "authorization": "private"},
        )
        await session.commit()
        task_id = task.id
        first_event_id = first.id

    with create_test_client(db_sessionmaker) as client:
        all_events = client.get(
            f"/local/tasks/{task_id}/events",
            params={"user_id": user_id},
        )
        resumed = client.get(
            f"/local/tasks/{task_id}/events",
            params={"user_id": user_id, "after_event_id": first_event_id},
        )

    assert all_events.status_code == 200
    events = all_events.json()["items"]
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[0]["event_id"] == first_event_id
    assert events[0]["task_id"] == task_id
    assert events[1]["type"] == "task.log.appended"
    assert "secret-token" not in str(events[1])
    assert "authorization" not in str(events[1]).lower()
    assert resumed.status_code == 200
    assert [event["sequence"] for event in resumed.json()["items"]] == [2]


@pytest.mark.asyncio
async def test_local_api_approval_decision_is_idempotent_and_reuses_backend_semantics(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(db_sessionmaker)
    async with db_sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="local",
            task_type="office",
            input_text="write a file",
            status=TaskStatus.WAITING_APPROVAL.value,
            result_text="需要审批。",
        )
        session.add(task)
        await session.flush()
        approval = Approval(
            task_id=task.id,
            tool_name="filesystem.write",
            approval_type="tool",
            subject="filesystem.write",
            request_summary="写入 README.md",
            status=ApprovalStatus.PENDING.value,
        )
        session.add(approval)
        await session.commit()
        task_id = task.id
        approval_id = approval.id

    with create_test_client(db_sessionmaker) as client:
        first = client.post(
            f"/local/tasks/{task_id}/approvals/{approval_id}",
            json={"user_id": user_id, "decision": "approve", "reason": "confirmed"},
        )
        second = client.post(
            f"/local/tasks/{task_id}/approvals/{approval_id}",
            json={"user_id": user_id, "decision": "approve", "reason": "confirmed again"},
        )

    assert first.status_code == 200
    assert isinstance(first.json()["queued"], bool)
    assert first.json()["task"]["status"] == "pending"
    assert first.json()["approval"]["status"] == "approved"
    assert second.status_code == 200
    assert second.json()["queued"] is False
    assert second.json()["task"]["status"] == "pending"


@pytest.mark.asyncio
async def test_local_settings_validation_keeps_security_policy_on_backend(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    with create_test_client(db_sessionmaker) as client:
        accepted = client.post(
            "/local/settings/validate",
            json={
                "api_base_url": "http://127.0.0.1:8000",
                "default_workdir": str(tmp_path),
                "default_model_class": "standard",
                "approval_policy": "ask",
            },
        )
        rejected_url = client.post(
            "/local/settings/validate",
            json={
                "api_base_url": "https://example.com",
                "default_workdir": str(tmp_path),
                "approval_policy": "ask",
            },
        )
        rejected_path = client.post(
            "/local/settings/validate",
            json={
                "api_base_url": "http://127.0.0.1:8000",
                "default_workdir": str(tmp_path / "missing"),
                "approval_policy": "ask",
            },
        )

    assert accepted.status_code == 200
    assert accepted.json()["ok"] is True
    assert accepted.json()["settings"]["default_workdir"] == str(tmp_path.resolve())
    assert "token" not in str(accepted.json()).lower()
    assert rejected_url.status_code == 400
    assert rejected_url.json()["error"]["code"] == "invalid_local_api_base_url"
    assert rejected_path.status_code == 400
    assert rejected_path.json()["error"]["code"] == "invalid_default_workdir"
