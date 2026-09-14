from __future__ import annotations

import ast
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"


def _tracked_production_modules() -> dict[str, Path]:
    """Return tracked backend production modules keyed by importable module name."""
    tracked = subprocess.check_output(
        ["git", "ls-files", "backend/**/*.py"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    modules: dict[str, Path] = {}
    for raw_path in tracked:
        path = PurePosixPath(raw_path)
        if path.parts[:2] == ("backend", "migrations"):
            continue
        parts = list(path.parts[1:])
        if parts[-1] == "__init__.py":
            parts.pop()
        else:
            parts[-1] = parts[-1][:-3]
        module = ".".join(parts)
        if module:
            modules[module] = ROOT / raw_path
    return modules


def _imports_for(module: str, path: Path) -> set[str]:
    """Resolve imports executed while one module is initialized.

    Type-only imports and imports nested in functions or classes do not participate
    in Python's partially initialized-module failures, so they are intentionally
    excluded from this audit.
    """
    parts = module.split(".")
    package = parts if path.name == "__init__.py" else parts[:-1]
    imports: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def collect(node: ast.stmt) -> None:
        """Collect imports that execute as part of module initialization."""
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
            return
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:]
                if node.level > 1:
                    base = base[: -(node.level - 1)]
                if node.module:
                    imports.add(".".join(base + node.module.split(".")))
                else:
                    imports.update(".".join(base + [alias.name]) for alias in node.names)
            elif node.module:
                imports.add(node.module)
            return
        if isinstance(node, ast.If):
            type_checking = (
                isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
            ) or (
                isinstance(node.test, ast.Attribute)
                and node.test.attr == "TYPE_CHECKING"
            )
            branches = node.orelse if type_checking else [*node.body, *node.orelse]
            for child in branches:
                collect(child)
            return
        if isinstance(node, ast.Try):
            for child in node.body:
                collect(child)
            for child in node.orelse:
                collect(child)
            for child in node.finalbody:
                collect(child)
            for handler in node.handlers:
                for child in handler.body:
                    collect(child)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for child in node.body:
                collect(child)

    for node in tree.body:
        collect(node)
    return imports


def _module_graph() -> dict[str, set[str]]:
    """Build a best-effort static dependency graph for tracked production modules."""
    modules = _tracked_production_modules()
    graph: dict[str, set[str]] = {module: set() for module in modules}
    for module, path in modules.items():
        for imported in _imports_for(module, path):
            candidate = imported
            while candidate and candidate not in modules:
                candidate = candidate.rpartition(".")[0]
            if candidate and candidate != module:
                graph[module].add(candidate)
    return graph


def _strongly_connected_components(
    graph: dict[str, set[str]],
) -> tuple[tuple[str, ...], ...]:
    """Return non-trivial strongly connected components in the import graph."""
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(module: str) -> None:
        """Visit one module using Tarjan's strongly connected component algorithm."""
        nonlocal index
        indexes[module] = index
        lowlinks[module] = index
        index += 1
        stack.append(module)
        on_stack.add(module)
        for dependency in graph[module]:
            if dependency not in indexes:
                visit(dependency)
                lowlinks[module] = min(lowlinks[module], lowlinks[dependency])
            elif dependency in on_stack:
                lowlinks[module] = min(lowlinks[module], indexes[dependency])
        if lowlinks[module] != indexes[module]:
            return
        component: list[str] = []
        while True:
            dependency = stack.pop()
            on_stack.remove(dependency)
            component.append(dependency)
            if dependency == module:
                break
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    for module in sorted(graph):
        if module not in indexes:
            visit(module)
    return tuple(components)


def _run_fresh_backend_import(source: str) -> None:
    """Import a package from a clean process without inheriting test PYTHONPATH."""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_application_modules_do_not_depend_on_app_package() -> None:
    """Application services must not import protocol or adapter modules from app."""
    forbidden: list[tuple[str, str]] = []
    for module, path in _tracked_production_modules().items():
        if not module.startswith("application."):
            continue
        for imported in _imports_for(module, path):
            if imported == "app" or imported.startswith("app."):
                forbidden.append((module, imported))
    assert forbidden == []


def test_audited_package_groups_are_not_import_time_cycles() -> None:
    """Type-only and deferred imports must not be misreported as import cycles."""
    groups = (
        ("infrastructure.telemetry", "observability"),
        ("channels.langbot", "workers"),
        ("agent.capabilities", "agent.skill_management", "tools"),
        ("application.user_memory", "memory"),
    )
    violations: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for component in _strongly_connected_components(_module_graph()):
        for group in groups:
            matched = {
                prefix
                for prefix in group
                if any(module == prefix or module.startswith(f"{prefix}.") for module in component)
            }
            if len(matched) > 1:
                violations.append((group, component))
    assert violations == []


def test_public_import_compatibility_survives_boundary_refactor() -> None:
    """Compatibility facades resolve only the requested audited public names."""
    _run_fresh_backend_import(
        "from application.command_catalog import parse_task_type\n"
        "from app.support.commands import parse_task_type as legacy_parse_task_type\n"
        "import observability\n"
        "import channels.langbot.service\n"
        "import workers.runtime\n"
        "import agent.skill_management\n"
        "import tools\n"
        "import application.user_memory\n"
        "import memory\n"
        "assert parse_task_type('/status task-1') == legacy_parse_task_type('/status task-1')\n"
    )


def test_nested_backend_cache_directory_is_not_present_or_tracked() -> None:
    """The nested backend cache path must never become source or runtime state."""
    tracked = subprocess.check_output(
        ["git", "ls-files", "backend/backend"],
        cwd=ROOT,
        text=True,
    )
    assert tracked == ""
    assert not (BACKEND_ROOT / "backend").exists()
