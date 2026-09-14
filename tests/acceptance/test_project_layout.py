from __future__ import annotations

from pathlib import Path
import subprocess
import tomllib
from collections import Counter


ROOT = Path(__file__).resolve().parents[2]


def test_project_has_clear_frontend_backend_layout() -> None:
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
        "spaces",
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
        "planning",
        "prompting",
        "review",
        "skill_management",
    ):
        assert (ROOT / "backend" / "agent" / layer / "__init__.py").is_file()
    for package in ("runtime", "tools", "memory", "application"):
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
        "artifacts.py",
        "base.py",
        "conversations.py",
        "enterprise.py",
        "enums.py",
        "evolution.py",
        "identity.py",
        "knowledge.py",
        "lifecycle_hooks.py",
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
        "enterprise.py",
        "governance.py",
        "redaction.py",
        "task_status.py",
        "tool_approval.py",
    ]
    assert (ROOT / "backend" / "application" / "__init__.py").is_file()
    assert (
        ROOT / "backend" / "application" / "artifact_lifecycle" / "__init__.py"
    ).is_file()
    assert (ROOT / "backend" / "application" / "task_execution" / "__init__.py").is_file()
    assert sorted(
        path.name
        for path in (ROOT / "backend" / "application" / "task_execution").glob("*.py")
    ) == [
        "__init__.py",
        "commands.py",
        "dispatch.py",
        "events.py",
        "executor.py",
        "lifecycle.py",
        "status.py",
    ]
    assert not (ROOT / "backend" / "tasks").exists()
    assert (ROOT / "backend" / "application" / "session_context" / "__init__.py").is_file()
    assert sorted(
        path.name
        for path in (ROOT / "backend" / "application" / "session_context").glob("*.py")
    ) == [
        "__init__.py",
        "context_blocks.py",
        "context_service.py",
        "context_snapshots.py",
        "conversations.py",
        "execution_workspaces.py",
        "resource_references.py",
        "spaces.py",
        "summary.py",
        "text.py",
    ]
    assert not (ROOT / "backend" / "session").exists()
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
    assert (ROOT / "backend" / "application" / "runtime_dependencies.py").is_file()
    assert not (ROOT / "backend" / "infrastructure" / "adapters" / "agent_runtime.py").exists()
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
    assert sorted(path.name for path in (ROOT / "backend" / "integrations").glob("*.py")) == [
        "__init__.py",
        "account_browser_sessions.py",
        "account_connection_tester.py",
        "account_connections.py",
        "connected_account_actions.py",
        "credential_cipher.py",
        "email_calendar_providers.py",
        "notification_delivery.py",
    ]
    for old_name in (
        "account_backed.py",
        "accounts.py",
        "browser_sessions.py",
        "connection_tester.py",
        "credentials.py",
        "notifications.py",
        "providers.py",
    ):
        assert not (ROOT / "backend" / "integrations" / old_name).exists()
    assert not (ROOT / "backend" / "models").exists()
    assert (ROOT / "backend" / "model_gateway").is_dir()
    assert (ROOT / "backend" / "migrations" / "versions").is_dir()
    assert (ROOT / "backend" / "memory" / "candidate_extraction.py").is_file()
    assert (ROOT / "backend" / "memory" / "candidate_pipeline.py").is_file()
    assert (ROOT / "backend" / "memory" / "release.py").is_file()
    assert not (ROOT / "backend" / "memory" / "user_memory").exists()
    assert (ROOT / "backend" / "application" / "user_memory" / "__init__.py").is_file()
    assert sorted(
        path.name for path in (ROOT / "backend" / "application" / "user_memory").glob("*.py")
    ) == [
        "__init__.py",
        "commands.py",
        "errors.py",
        "semantic_sync.py",
        "service.py",
    ]
    assert not (ROOT / "backend" / "user_memory").exists()
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
    assert (ROOT / "backend" / "workers" / "lifecycle.py").is_file()
    assert not (ROOT / "backend" / "workers" / "composition").exists()
    assert (ROOT / "backend" / "resources" / "prompts" / "README.md").is_file()
    assert (ROOT / "backend" / "resources" / "config").is_dir()
    assert (ROOT / "backend" / "resources" / "skillpacks").is_dir()
    assert (ROOT / "frontend" / "desktop" / "package.json").is_file()
    assert not (ROOT / "legacy" / "desktop-qt").exists()

    assert not (ROOT / "backend" / "assistant_api").exists()
    assert not (ROOT / "backend" / "api").exists()
    assert not (ROOT / "backend" / "packages").exists()
    assert not (ROOT / "backend" / "prompts").exists()
    assert not (ROOT / "backend" / "skills").exists()
    assert not (ROOT / "backend" / "agent" / "core").exists()
    assert not (ROOT / "backend" / "agent" / "tool_management").exists()
    assert not (ROOT / "backend" / "agent" / "memory").exists()
    assert not (ROOT / "backend" / "agent" / "modeling").exists()
    assert (ROOT / "backend" / "agent" / "prompting" / "model_contract.py").is_file()
    assert not (ROOT / "backend" / "knowledge").exists()
    assert not (ROOT / "backend" / "notifications").exists()
    assert not (ROOT / "backend" / "scheduler").exists()
    assert not (ROOT / "backend" / "capabilities").exists()
    assert not (ROOT / "backend" / "common").exists()
    assert not (ROOT / "backend" / "config").exists()
    assert not (ROOT / "backend" / "policies").exists()
    assert not (ROOT / "apps" / "api").exists()
    assert not (ROOT / "apps" / "desktop-web").exists()
    assert not (ROOT / "apps" / "desktop").exists()


def test_first_party_backend_imports_use_application_service_paths() -> None:
    stale_imports: list[str] = []
    stale_prefixes = (
        "from tasks",
        "import tasks",
        "from session",
        "import application.session_context",
        "from user_memory",
        "import application.user_memory",
    )
    for path in (ROOT / "backend").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(stale_prefixes):
                stale_imports.append(f"{path.relative_to(ROOT)}:{line_number}: {stripped}")

    assert stale_imports == []


def test_agent_model_contract_boundary_is_not_model_gateway() -> None:
    contract = ROOT / "backend" / "agent" / "prompting" / "model_contract.py"
    gateway = ROOT / "backend" / "model_gateway" / "agent_model.py"
    assert contract.is_file()
    assert gateway.is_file()
    assert not (ROOT / "backend" / "agent" / "modeling").exists()

    contract_text = contract.read_text(encoding="utf-8")
    assert "class AgentModelProtocol" in contract_text
    assert "def parse_agent_decision" in contract_text
    assert "AgentGatewayModel" not in contract_text

    gateway_text = gateway.read_text(encoding="utf-8")
    assert "class AgentGatewayModel" in gateway_text
    assert "from agent.prompting.model_contract" in gateway_text


def test_infrastructure_does_not_export_agent_runtime_dependencies() -> None:
    adapters_init = ROOT / "backend" / "infrastructure" / "adapters" / "__init__.py"
    assert adapters_init.is_file()
    text = adapters_init.read_text(encoding="utf-8")
    assert "agent_runtime" not in text
    assert "SqlAlchemyConversationContextPort" not in text
    assert "SqlAlchemyExecutionTracePort" not in text
    assert "SqlAlchemyTaskLifecyclePort" not in text
    assert "SqlAlchemyUserLookupPort" not in text


def _git_ignored_paths(paths: list[Path]) -> set[Path]:
    """Return only paths Git classifies as ignored runtime or local state."""
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=ROOT,
        input="\n".join(str(path.relative_to(ROOT)) for path in paths),
        text=True,
        capture_output=True,
        check=False,
    )
    return {ROOT / value for value in result.stdout.splitlines()}


def test_backend_directory_basenames_are_unique() -> None:
    candidates = [
        path
        for path in (ROOT / "backend").rglob("*")
        if path.is_dir()
        and path.name not in {"__pycache__"}
        and not any(part.startswith(".") for part in path.relative_to(ROOT / "backend").parts)
    ]
    ignored = _git_ignored_paths(candidates)
    directories = [path for path in candidates if path not in ignored]
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

    for directory, task_type in (("memory_command", "memory"), ("status_command", "status")):
        readme = features_root / directory / "README.md"
        definition = features_root / directory / "definition.py"
        assert readme.is_file()
        assert definition.is_file()
        text = readme.read_text(encoding="utf-8")
        assert f"`{task_type}`" in text
        assert "backend/features" in text
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
    pyproject_path = ROOT / "backend" / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    alembic = (ROOT / "alembic.ini").read_text(encoding="utf-8")

    assert pyproject_path.is_file()
    assert (ROOT / "backend" / "uv.lock").is_file()
    assert (ROOT / "backend" / ".python-version").is_file()
    assert not (ROOT / "pyproject.toml").exists()
    assert not (ROOT / "uv.lock").exists()
    assert not (ROOT / ".python-version").exists()
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "app",
        "application",
        "channels",
        "domain",
        "agent",
        "runtime",
        "tools",
        "memory",
        "evaluation",
        "features",
        "infrastructure",
        "integrations",
        "observability",
        "rag",
        "model_gateway",
        "workers",
    ]
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["dev-mode-dirs"] == [
        ".",
    ]
    assert pyproject["tool"]["pytest"]["ini_options"]["pythonpath"] == [
        ".",
        "..",
    ]
    assert "COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./backend/" in dockerfile
    assert "WORKDIR /app/backend" in dockerfile
    assert "app.main:app" in dockerfile
    assert "cd backend && uv sync" in readme
    assert "PYTHONPATH=. uv --project backend run pytest" in readme
    assert "script_location = backend/migrations" in alembic
    assert "prepend_sys_path = backend" in alembic


def test_backend_wheel_smoke_isolated_from_source_imports() -> None:
    smoke = ROOT / "scripts" / "smoke" / "wheel_install.py"
    text = smoke.read_text(encoding="utf-8")

    assert smoke.is_file()
    assert '"--wheel"' in text
    assert '"-I"' in text
    assert "import app.main; import features.catalog" in text


def test_application_executor_owns_utility_task_dispatch_boundary() -> None:
    executor = ROOT / "backend" / "application" / "task_execution" / "executor.py"
    assert executor.is_file()

    executor_text = executor.read_text(encoding="utf-8")
    assert "class TaskExecutionService" in executor_text
    assert 'task.task_type == "memory"' in executor_text
    assert 'task.task_type == "status"' in executor_text

    runner_text = (ROOT / "backend" / "runtime" / "runner_harness.py").read_text(
        encoding="utf-8"
    )
    assert 'task.task_type == "memory"' not in runner_text
    assert 'task.task_type == "status"' not in runner_text


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


def test_utility_slash_commands_are_registered_through_feature_catalog() -> None:
    from app.support.commands import parse_task_type
    from features import FEATURE_COMMANDS, planning_task_types

    assert FEATURE_COMMANDS["/memory"] == "memory"
    assert FEATURE_COMMANDS["/status"] == "status"
    assert parse_task_type("/memory list") == "memory"
    assert parse_task_type("/status task-1") == "status"
    assert "memory" not in planning_task_types()
    assert "status" not in planning_task_types()
