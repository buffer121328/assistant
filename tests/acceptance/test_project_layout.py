from __future__ import annotations

from pathlib import Path
import tomllib
from collections import Counter


ROOT = Path(__file__).resolve().parents[2]


def test_project_has_clear_frontend_backend_legacy_layout() -> None:
    assert (ROOT / "backend" / "app").is_dir()
    assert sorted(path.name for path in (ROOT / "backend" / "app").glob("*.py")) == [
        "__init__.py",
        "dependencies.py",
        "main.py",
    ]
    assert (ROOT / "backend" / "app" / "api" / "router.py").is_file()
    assert (ROOT / "backend" / "app" / "api" / "routers" / "__init__.py").is_file()
    assert (ROOT / "backend" / "app" / "api" / "schemas" / "__init__.py").is_file()
    for router in (
        "accounts",
        "capabilities",
        "conversations",
        "knowledge",
        "memories",
        "model_chat",
        "notifications",
        "skills",
        "tasks",
    ):
        assert (ROOT / "backend" / "app" / "api" / "routers" / f"{router}.py").is_file()
    assert (ROOT / "backend" / "agent").is_dir()
    assert sorted(path.name for path in (ROOT / "backend" / "agent").glob("*.py")) == [
        "__init__.py",
        "ports.py",
    ]
    for layer in (
        "core",
        "governance",
        "memory",
        "modeling",
        "planning",
        "review",
        "skill_management",
        "tool_management",
    ):
        assert (ROOT / "backend" / "agent" / layer / "__init__.py").is_file()
    assert (ROOT / "backend" / "channels" / "langbot" / "router.py").is_file()
    assert (ROOT / "backend" / "channels" / "desktop" / "router.py").is_file()
    assert (ROOT / "backend" / "domain").is_dir()
    assert (ROOT / "backend" / "infrastructure").is_dir()
    assert (ROOT / "backend" / "integrations").is_dir()
    assert (ROOT / "backend" / "knowledge").is_dir()
    assert (ROOT / "backend" / "model_gateway").is_dir()
    assert (ROOT / "backend" / "migrations" / "versions").is_dir()
    assert (ROOT / "backend" / "scheduler").is_dir()
    assert (ROOT / "backend" / "resources" / "prompts" / "README.md").is_file()
    assert (ROOT / "backend" / "resources" / "skillpacks").is_dir()
    assert (ROOT / "backend" / "config").is_dir()
    assert (ROOT / "frontend" / "desktop" / "package.json").is_file()
    assert (ROOT / "legacy" / "desktop-qt" / "assistant_desktop").is_dir()

    assert not (ROOT / "backend" / "assistant_api").exists()
    assert not (ROOT / "backend" / "api").exists()
    assert not (ROOT / "backend" / "packages").exists()
    assert not (ROOT / "backend" / "prompts").exists()
    assert not (ROOT / "backend" / "skills").exists()
    assert not (ROOT / "backend" / "tools").exists()
    assert not (ROOT / "backend" / "memory").exists()
    assert not (ROOT / "apps" / "api").exists()
    assert not (ROOT / "apps" / "desktop-web").exists()
    assert not (ROOT / "apps" / "desktop").exists()


def test_backend_directory_basenames_are_unique() -> None:
    directories = [
        path
        for path in (ROOT / "backend").rglob("*")
        if path.is_dir()
        and path.name not in {"__pycache__"}
        and not path.name.startswith(".")
    ]
    duplicates = {
        name: count
        for name, count in Counter(path.name for path in directories).items()
        if count > 1
    }

    assert duplicates == {}


def test_backend_features_make_core_agent_scenarios_visible() -> None:
    features_root = ROOT / "backend" / "features"
    for task_type in ("plan", "learn", "daily", "office"):
        readme = features_root / task_type / "README.md"
        definition = features_root / task_type / "definition.py"
        assert readme.is_file()
        assert definition.is_file()
        text = readme.read_text(encoding="utf-8")
        assert f"`{task_type}`" in text
        assert "backend/features" in text
        assert "backend/agent/planning/profiles.py" in text
        assert "backend/resources/prompts" in text
        assert "backend/resources/skillpacks" in text
        assert "tests/acceptance" in text

    index = (features_root / "README.md").read_text(encoding="utf-8")
    assert "backend/features/<task_type>" in index
    assert "apps/" not in index


def test_backend_features_are_runtime_wiring_not_only_documentation() -> None:
    commands = (ROOT / "backend" / "app" / "support" / "commands.py").read_text(
        encoding="utf-8"
    )
    profiles = (
        ROOT / "backend" / "agent" / "planning" / "profiles.py"
    ).read_text(encoding="utf-8")
    catalog = (ROOT / "backend" / "features" / "catalog.py").read_text(
        encoding="utf-8"
    )

    assert "FEATURE_COMMANDS" in commands
    assert "feature_for_task_type" in profiles
    assert "CORE_FEATURES" in catalog


def test_runtime_metadata_uses_new_layout_paths() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    alembic = (ROOT / "alembic.ini").read_text(encoding="utf-8")

    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "backend/app",
        "backend/channels",
        "backend/domain",
        "backend/agent",
        "backend/capabilities",
        "backend/evaluation",
        "backend/infrastructure",
        "backend/integrations",
        "backend/knowledge",
        "backend/model_gateway",
        "backend/notifications",
        "backend/observability",
        "backend/scheduler",
        "backend/workers",
    ]
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["dev-mode-dirs"] == [
        "backend",
    ]
    assert pyproject["tool"]["pytest"]["ini_options"]["pythonpath"] == [
        "backend",
        "legacy/desktop-qt",
        ".",
    ]
    assert "--app-dir\", \"backend\"" in dockerfile
    assert "app.main:app" in dockerfile
    assert "frontend/desktop" in readme
    assert "backend/features/<task_type>" in readme
    assert "backend/channels/langbot" in readme
    assert "backend/channels/desktop" in readme
    assert "workers.worker:celery_app" in readme
    assert "script_location = backend/migrations" in alembic
    assert "prepend_sys_path = backend" in alembic
