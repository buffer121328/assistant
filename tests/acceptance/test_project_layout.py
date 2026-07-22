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
        "governance",
        "modeling",
        "planning",
        "review",
        "skill_management",
    ):
        assert (ROOT / "backend" / "agent" / layer / "__init__.py").is_file()
    for package in ("runtime", "tools", "memory"):
        assert (ROOT / "backend" / package / "__init__.py").is_file()
    assert (ROOT / "backend" / "channels" / "langbot" / "router.py").is_file()
    assert (ROOT / "backend" / "channels" / "desktop" / "router.py").is_file()
    assert (ROOT / "backend" / "domain").is_dir()
    assert (ROOT / "backend" / "infrastructure").is_dir()
    assert (ROOT / "backend" / "integrations").is_dir()
    assert (ROOT / "backend" / "knowledge").is_dir()
    assert (ROOT / "backend" / "models").is_dir()
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
    assert not (ROOT / "backend" / "agent" / "core").exists()
    assert not (ROOT / "backend" / "agent" / "tool_management").exists()
    assert not (ROOT / "backend" / "agent" / "memory").exists()
    assert not (ROOT / "backend" / "model_gateway").exists()
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


def test_rag_is_primary_implementation_and_knowledge_is_compatibility_shim() -> None:
    rag_service = (ROOT / "backend" / "rag" / "service.py").read_text(
        encoding="utf-8"
    )
    rag_extractors = (ROOT / "backend" / "rag" / "extractors.py").read_text(
        encoding="utf-8"
    )
    knowledge_init = (ROOT / "backend" / "knowledge" / "__init__.py").read_text(
        encoding="utf-8"
    )
    knowledge_service = (ROOT / "backend" / "knowledge" / "service.py").read_text(
        encoding="utf-8"
    )
    knowledge_extractors = (
        ROOT / "backend" / "knowledge" / "extractors.py"
    ).read_text(encoding="utf-8")

    assert "class KnowledgeService:" in rag_service
    assert "def extract_text(" in rag_extractors
    assert "from .extractors import" in rag_service
    assert "from knowledge" not in rag_service
    assert "from rag import" in knowledge_init
    assert "from rag.service import" in knowledge_service
    assert "from rag.extractors import" in knowledge_extractors
    assert "class KnowledgeService:" not in knowledge_init
    assert "class KnowledgeService:" not in knowledge_service
    assert "def extract_text(" not in knowledge_extractors


def test_first_party_runtime_imports_rag_not_legacy_knowledge_package() -> None:
    first_party_paths = [
        ROOT / "backend" / "workers" / "runtime.py",
        ROOT / "backend" / "app" / "api" / "routers" / "knowledge.py",
        ROOT / "backend" / "tools" / "knowledge.py",
        ROOT / "backend" / "evaluation" / "rag_retrieval.py",
    ]

    for path in first_party_paths:
        text = path.read_text(encoding="utf-8")
        assert "from rag import" in text, path
        assert "from knowledge import" not in text, path


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
        "backend/runtime",
        "backend/tools",
        "backend/memory",
        "backend/capabilities",
        "backend/evaluation",
        "backend/infrastructure",
        "backend/integrations",
        "backend/knowledge",
        "backend/rag",
        "backend/models",
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


def test_builtin_skill_runtime_lookups_use_resource_skillpacks() -> None:
    runtime = (ROOT / "backend" / "workers" / "runtime.py").read_text(
        encoding="utf-8"
    )
    assert 'BUILTIN_SKILL_ROOT = Path(__file__).resolve().parents[1] / "resources" / "skillpacks"' in runtime
    assert "builtin_root=BUILTIN_SKILL_ROOT" in runtime
    assert "build_default_registry(\n            BUILTIN_SKILL_ROOT\n        )" in runtime
    assert ' / "skills"' not in runtime


def test_browser_state_root_is_not_runtime_configuration() -> None:
    for path in (
        ROOT / "backend" / "infrastructure" / "config.py",
        ROOT / ".env.example",
        ROOT / "docker-compose.yml",
    ):
        assert "BROWSER_STATE_ROOT" not in path.read_text(encoding="utf-8")


def test_readme_distinguishes_builtin_resources_from_mutable_var_roots() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "backend/resources" in readme
    assert "var/" in readme
    assert "内置源资源" in readme
    assert "可变运行时根目录" in readme
