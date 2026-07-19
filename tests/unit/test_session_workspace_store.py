from pathlib import Path

import pytest

from agent.tool_management.workspace import (
    SessionWorkspacePathError,
    SessionWorkspaceStore,
)


def test_create_session_workspace_has_standard_areas(tmp_path: Path) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    workspace = store.create(session_id="session-123")

    assert (
        workspace.root
        == (tmp_path / "workspace" / "sessions" / "session-123").resolve()
    )
    assert workspace.input_dir.is_dir()
    assert workspace.work_dir.is_dir()
    assert workspace.output_dir.is_dir()
    assert workspace.audit_dir.is_dir()


def test_reserve_input_path_stays_inside_session_workspace(tmp_path: Path) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    input_path = store.reserve_input(session_id="session_123", filename="source.txt")

    assert (
        input_path
        == (
            tmp_path / "workspace" / "sessions" / "session_123" / "input" / "source.txt"
        ).resolve()
    )


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
        store.reserve_task_output(
            session_id="session-123",
            task_id="task-1",
            filename=f"{'a' * 129}.txt",
        )


def test_rejects_symlink_escape_for_area(tmp_path: Path) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(session_id="session-123")
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace.work_dir.rmdir()
    workspace.work_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SessionWorkspacePathError):
        store.reserve_task_work(
            session_id="session-123",
            task_id="task-1",
            filename="escaped.txt",
        )


def test_rejects_symlink_escape_for_existing_file_target(tmp_path: Path) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(session_id="session-123")
    task_dir = workspace.output_dir / "task-1"
    task_dir.mkdir()
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")
    target = task_dir / "result.txt"
    target.symlink_to(outside_file)

    with pytest.raises(SessionWorkspacePathError):
        store.reserve_task_output(
            session_id="session-123",
            task_id="task-1",
            filename="result.txt",
        )


def test_task_workspace_legacy_aliases_are_removed() -> None:
    import agent.tool_management.workspace as workspace

    assert not hasattr(workspace, "TaskWorkspaceStore")
    assert not hasattr(workspace, "TaskWorkspace")
    assert not hasattr(workspace, "TaskWorkspacePathError")


@pytest.mark.parametrize(
    "method_name",
    ["reserve_work", "reserve_output", "reserve_audit", "path_for", "task_path_for"],
)
def test_flat_work_output_audit_reserve_apis_are_removed(method_name: str) -> None:
    assert not hasattr(SessionWorkspaceStore, method_name)


def test_task_scoped_paths_prevent_same_session_filename_collisions(
    tmp_path: Path,
) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    task_1 = store.reserve_task_output(
        session_id="session-123",
        task_id="task-1",
        filename="summary.md",
    )
    task_2 = store.reserve_task_output(
        session_id="session-123",
        task_id="task-2",
        filename="summary.md",
    )
    work = store.reserve_task_work(
        session_id="session-123",
        task_id="task-1",
        filename="parsed.json",
    )
    audit = store.reserve_task_audit(
        session_id="session-123",
        task_id="task-1",
        filename="tool-call.json",
    )

    assert task_1 != task_2
    assert (
        task_1
        == (
            tmp_path
            / "workspace"
            / "sessions"
            / "session-123"
            / "output"
            / "task-1"
            / "summary.md"
        ).resolve()
    )
    assert (
        task_2
        == (
            tmp_path
            / "workspace"
            / "sessions"
            / "session-123"
            / "output"
            / "task-2"
            / "summary.md"
        ).resolve()
    )
    assert (
        work
        == (
            tmp_path
            / "workspace"
            / "sessions"
            / "session-123"
            / "work"
            / "task-1"
            / "parsed.json"
        ).resolve()
    )
    assert (
        audit
        == (
            tmp_path
            / "workspace"
            / "sessions"
            / "session-123"
            / "audit"
            / "task-1"
            / "tool-call.json"
        ).resolve()
    )


@pytest.mark.parametrize(
    "task_id", ["", "../evil", "task/evil", "/absolute", "bad task", ".", ".."]
)
def test_task_scoped_paths_reject_invalid_task_id(tmp_path: Path, task_id: str) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    with pytest.raises(SessionWorkspacePathError):
        store.reserve_task_work(
            session_id="session-123",
            task_id=task_id,
            filename="result.txt",
        )


def test_task_scoped_paths_reject_task_directory_symlink_escape(tmp_path: Path) -> None:
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(session_id="session-123")
    outside = tmp_path / "outside"
    outside.mkdir()
    task_dir = workspace.output_dir / "task-1"
    task_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SessionWorkspacePathError):
        store.reserve_task_output(
            session_id="session-123",
            task_id="task-1",
            filename="summary.md",
        )
