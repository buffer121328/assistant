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
        "capabilities.py",
        "ports.py",
    ]
    for layer in (
        "governance",
        "modeling",
        "planning",
        "prompting",
        "review",
        "skill_management",
    ):
        assert (ROOT / "backend" / "agent" / layer / "__init__.py").is_file()
    for package in ("runtime", "tools", "memory", "session", "tasks"):
        assert (ROOT / "backend" / package / "__init__.py").is_file()
    assert not (ROOT / "backend" / "tools" / "builtin" / "search.py").exists()
    assert not (
        ROOT / "backend" / "tools" / "builtin" / "workspace_context.py"
    ).exists()
    assert (ROOT / "backend" / "tools" / "builtin" / "search" / "__init__.py").is_file()
    assert (
        ROOT / "backend" / "tools" / "builtin" / "workspace_context" / "__init__.py"
    ).is_file()
    assert (ROOT / "backend" / "tools" / "builtin" / "schedule" / "__init__.py").is_file()
    assert (ROOT / "backend" / "tools" / "builtin" / "agent_memory" / "__init__.py").is_file()
    assert not (ROOT / "backend" / "tools" / "builtin" / "memory_tools.py").exists()
    assert not (ROOT / "backend" / "tools" / "builtin" / "schedule_tools.py").exists()
    assert sorted(
        path.name for path in (ROOT / "backend" / "tools" / "builtin" / "schedule").glob("*.py")
    ) == [
        "__init__.py",
        "constants.py",
        "descriptors.py",
        "payloads.py",
        "service.py",
        "time_utils.py",
    ]
    assert (ROOT / "backend" / "channels" / "langbot" / "router.py").is_file()
    assert (ROOT / "backend" / "channels" / "desktop" / "router.py").is_file()
    assert (ROOT / "backend" / "domain").is_dir()
    assert sorted(path.name for path in (ROOT / "backend" / "domain").glob("*.py")) == [
        "__init__.py",
    ]
    assert not (ROOT / "backend" / "domain" / "model_gateway.py").exists()
    assert (ROOT / "backend" / "domain" / "policies" / "__init__.py").is_file()
    assert sorted(
        path.name for path in (ROOT / "backend" / "domain" / "models").glob("*.py")
    ) == [
        "__init__.py",
        "base.py",
        "conversations.py",
        "enums.py",
        "evolution.py",
        "identity.py",
        "knowledge.py",
        "memory.py",
        "notifications.py",
        "observability.py",
        "schedules.py",
        "tasks.py",
    ]
    assert sorted(
        path.name for path in (ROOT / "backend" / "domain" / "policies").glob("*.py")
    ) == [
        "__init__.py",
        "approval_requests.py",
        "redaction.py",
        "task_status.py",
        "tool_approval.py",
    ]
    assert not (ROOT / "backend" / "application").exists()
    assert sorted(path.name for path in (ROOT / "backend" / "tasks").glob("*.py")) == [
        "__init__.py",
        "commands.py",
        "dispatch.py",
        "events.py",
        "lifecycle.py",
        "status.py",
    ]
    assert sorted(path.name for path in (ROOT / "backend" / "session").glob("*.py")) == [
        "__init__.py",
        "conversations.py",
        "memory_blocks.py",
        "memory_service.py",
        "summary.py",
        "text.py",
    ]
    assert (ROOT / "backend" / "infrastructure").is_dir()
    for old_module in (
        "agent_ports.py",
        "auth.py",
        "checkpoints.py",
        "config.py",
        "database.py",
        "logging.py",
        "observability.py",
        "repositories.py",
    ):
        assert not (ROOT / "backend" / "infrastructure" / old_module).exists()
    assert (ROOT / "backend" / "infrastructure" / "adapters" / "agent_runtime.py").is_file()
    assert (ROOT / "backend" / "infrastructure" / "persistence" / "database.py").is_file()
    assert (ROOT / "backend" / "infrastructure" / "persistence" / "checkpoints.py").is_file()
    assert (ROOT / "backend" / "infrastructure" / "security" / "auth.py").is_file()
    assert (ROOT / "backend" / "infrastructure" / "settings" / "config.py").is_file()
    assert (ROOT / "backend" / "infrastructure" / "telemetry" / "logging.py").is_file()
    assert (ROOT / "backend" / "infrastructure" / "telemetry" / "observability.py").is_file()
    assert sorted(
        path.name
        for path in (ROOT / "backend" / "infrastructure" / "repositories").glob("*.py")
    ) == [
        "__init__.py",
        "approvals.py",
        "memory.py",
        "messages.py",
        "model_logs.py",
        "skill_audit.py",
        "tasks.py",
        "tool_logs.py",
    ]
    assert (ROOT / "backend" / "integrations").is_dir()
    assert (ROOT / "backend" / "integrations" / "accounts.py").is_file()
    assert (ROOT / "backend" / "integrations" / "notifications.py").is_file()
    assert not (ROOT / "backend" / "models").exists()
    assert (ROOT / "backend" / "model_gateway").is_dir()
    assert (ROOT / "backend" / "migrations" / "versions").is_dir()
    assert (ROOT / "backend" / "memory" / "candidate_extraction.py").is_file()
    assert (ROOT / "backend" / "memory" / "candidate_pipeline.py").is_file()
    assert (ROOT / "backend" / "memory" / "release.py").is_file()
    assert (ROOT / "backend" / "memory" / "user_memory" / "__init__.py").is_file()
    assert sorted(
        path.name for path in (ROOT / "backend" / "memory" / "user_memory").glob("*.py")
    ) == [
        "__init__.py",
        "commands.py",
        "errors.py",
        "semantic.py",
        "service.py",
    ]
    assert not (ROOT / "backend" / "runtime" / "langgraph_executor").exists()
    for module in (
        "langgraph_approval_flow.py",
        "langgraph_executor.py",
        "langgraph_executor_core.py",
        "langgraph_graph.py",
        "langgraph_model_flow.py",
        "langgraph_payloads.py",
        "langgraph_review_flow.py",
        "langgraph_runtime_helpers.py",
        "langgraph_state.py",
        "langgraph_tool_flow.py",
    ):
        assert (ROOT / "backend" / "runtime" / module).is_file()
    for module in (
        "runner.py",
        "runner_boundary.py",
        "runner_default_executor.py",
        "runner_events.py",
        "runner_harness.py",
        "runner_types.py",
    ):
        assert (ROOT / "backend" / "runtime" / module).is_file()
    assert (ROOT / "backend" / "workers" / "heartbeat.py").is_file()
    assert (ROOT / "backend" / "resources" / "prompts" / "README.md").is_file()
    assert (ROOT / "backend" / "resources" / "config").is_dir()
    assert (ROOT / "backend" / "resources" / "skillpacks").is_dir()
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
    assert not (ROOT / "backend" / "knowledge").exists()
    assert not (ROOT / "backend" / "notifications").exists()
    assert not (ROOT / "backend" / "scheduler").exists()
    assert not (ROOT / "backend" / "capabilities").exists()
    assert not (ROOT / "backend" / "common").exists()
    assert not (ROOT / "backend" / "config").exists()
    assert not (ROOT / "backend" / "observability").exists()
    assert not (ROOT / "backend" / "policies").exists()
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
    allowed_duplicate_paths: dict[str, set[Path]] = {}
    duplicates = {
        name: sorted(
            str(path.relative_to(ROOT)) for path in directories if path.name == name
        )
        for name, count in Counter(path.name for path in directories).items()
        if count > 1
        and set(path for path in directories if path.name == name)
        != allowed_duplicate_paths.get(name, set())
    }

    assert duplicates == {}


def test_rag_is_primary_implementation_without_legacy_knowledge_package() -> None:
    rag_service = (ROOT / "backend" / "rag" / "service.py").read_text(encoding="utf-8")
    rag_extractors = (ROOT / "backend" / "rag" / "extractors.py").read_text(
        encoding="utf-8"
    )

    assert "class KnowledgeService:" in rag_service
    assert "def extract_text(" in rag_extractors
    assert "from .extractors import" in rag_service
    assert "from knowledge" not in rag_service
    assert not (ROOT / "backend" / "knowledge").exists()


def test_first_party_runtime_imports_rag_not_legacy_knowledge_package() -> None:
    first_party_paths = [
        ROOT / "backend" / "workers" / "runtime.py",
        ROOT / "backend" / "app" / "api" / "routers" / "knowledge.py",
        ROOT / "backend" / "tools" / "builtin" / "knowledge.py",
        ROOT / "backend" / "evaluation" / "rag_retrieval.py",
    ]

    for path in first_party_paths:
        text = path.read_text(encoding="utf-8")
        assert "from rag import" in text, path
        assert "from knowledge" not in text, path


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
    profiles = (ROOT / "backend" / "agent" / "planning" / "profiles.py").read_text(
        encoding="utf-8"
    )
    catalog = (ROOT / "backend" / "features" / "catalog.py").read_text(encoding="utf-8")

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
        "backend/session",
        "backend/tasks",
        "backend/channels",
        "backend/domain",
        "backend/agent",
        "backend/runtime",
        "backend/tools",
        "backend/memory",
        "backend/evaluation",
        "backend/infrastructure",
        "backend/integrations",
        "backend/rag",
        "backend/model_gateway",
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
    assert '--app-dir", "backend"' in dockerfile
    assert "app.main:app" in dockerfile
    assert "frontend/desktop" in readme
    assert "backend/features/<task_type>" in readme
    assert "backend/channels/langbot" in readme
    assert "backend/channels/desktop" in readme
    assert "workers.worker:celery_app" in readme
    assert "script_location = backend/migrations" in alembic
    assert "prepend_sys_path = backend" in alembic


def test_builtin_skill_runtime_lookups_use_resource_skillpacks() -> None:
    runtime = (ROOT / "backend" / "workers" / "runtime.py").read_text(encoding="utf-8")
    assert (
        'BUILTIN_SKILL_ROOT = Path(__file__).resolve().parents[1] / "resources" / "skillpacks"'
        in runtime
    )
    assert "builtin_root=BUILTIN_SKILL_ROOT" in runtime
    assert (
        "build_default_registry(\n            BUILTIN_SKILL_ROOT\n        )" in runtime
    )
    assert ' / "skills"' not in runtime


def test_browser_state_root_is_not_runtime_configuration() -> None:
    for path in (
        ROOT / "backend" / "infrastructure" / "settings" / "config.py",
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
