from importlib import import_module


EXPECTED_EXPORTS = {
    "DEFAULT_DENY_GLOBS",
    "READONLY_SHELL_VERSION",
    "WORKSPACE_TOOL_VERSION",
    "ReadonlyShellResult",
    "ReadonlyShellRunner",
    "WorkspaceContextError",
    "WorkspaceContextStore",
    "WorkspaceEntry",
    "WorkspaceSearchMatch",
    "build_workspace_tool_descriptors",
    "build_workspace_tool_specs",
    "parse_deny_globs",
}


def test_workspace_context_public_exports_remain_available() -> None:
    """Workspace context symbols remain available from tools.builtin.workspace_context."""
    workspace_context = import_module("tools.builtin.workspace_context")

    assert set(workspace_context.__all__) == EXPECTED_EXPORTS
    for name in EXPECTED_EXPORTS:
        assert getattr(workspace_context, name) is not None
