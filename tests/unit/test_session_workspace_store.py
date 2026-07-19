from pathlib import Path

import pytest

from agent.tool_management.workspace import (
    SessionWorkspacePathError,
    SessionWorkspaceStore,
)


def test_create_session_workspace_has_standard_areas(tmp_path: Path) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    workspace = store.create(session_id="session-123")

    assert workspace.root == (tmp_path / "workspace" / "sessions" / "session-123").resolve()
    assert workspace.input_dir.is_dir()
    assert workspace.work_dir.is_dir()
    assert workspace.output_dir.is_dir()
    assert workspace.audit_dir.is_dir()


def test_reserve_area_paths_stay_inside_workspace(tmp_path: Path) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    input_path = store.reserve_input(session_id="session_123", filename="source.txt")
    work_path = store.reserve_work(session_id="session_123", filename="parsed.json")
    output_path = store.reserve_output(session_id="session_123", filename="summary.md")
    audit_path = store.reserve_audit(session_id="session_123", filename="tool-call.json")

    assert input_path == (
        tmp_path / "workspace" / "sessions" / "session_123" / "input" / "source.txt"
    ).resolve()
    assert work_path == (
        tmp_path / "workspace" / "sessions" / "session_123" / "work" / "parsed.json"
    ).resolve()
    assert output_path == (
        tmp_path / "workspace" / "sessions" / "session_123" / "output" / "summary.md"
    ).resolve()
    assert audit_path == (
        tmp_path / "workspace" / "sessions" / "session_123" / "audit" / "tool-call.json"
    ).resolve()


@pytest.mark.parametrize(
    "session_id",
    ["", "../evil", "session/evil", "/absolute", "bad session", ".", ".."],
)
def test_rejects_invalid_session_id(tmp_path: Path, session_id: str) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    with pytest.raises(SessionWorkspacePathError):
        store.create(session_id=session_id)


@pytest.mark.parametrize(
    "filename",
    ["", "../evil.txt", "nested/file.txt", "/absolute.txt", ".", "..", "bad\x00.txt"],
)
def test_rejects_invalid_filename(tmp_path: Path, filename: str) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    with pytest.raises(SessionWorkspacePathError):
        store.reserve_input(session_id="session-123", filename=filename)


def test_rejects_too_long_filename(tmp_path: Path) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    with pytest.raises(SessionWorkspacePathError):
        store.reserve_output(session_id="session-123", filename=f"{'a' * 129}.txt")


def test_rejects_symlink_escape_for_area(tmp_path: Path) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(session_id="session-123")
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace.work_dir.rmdir()
    workspace.work_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SessionWorkspacePathError):
        store.reserve_work(session_id="session-123", filename="escaped.txt")


def test_rejects_symlink_escape_for_existing_file_target(tmp_path: Path) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(session_id="session-123")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")
    target = workspace.output_dir / "result.txt"
    target.symlink_to(outside_file)

    with pytest.raises(SessionWorkspacePathError):
        store.reserve_output(session_id="session-123", filename="result.txt")


def test_task_workspace_names_remain_backward_compatible(tmp_path: Path) -> None:
    from agent.tool_management.workspace import TaskWorkspacePathError, TaskWorkspaceStore

    store = TaskWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(task_id="session-123")

    assert workspace.root.name == "session-123"
    assert TaskWorkspacePathError is SessionWorkspacePathError
