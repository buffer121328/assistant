from hashlib import sha256
import json
from pathlib import Path
from typing import cast

import pytest

from tools.builtin.workspace import (
    WorkspaceSourceFile,
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


def test_resource_input_and_task_activation_preserve_compatibility_layout(
    tmp_path: Path,
) -> None:
    """Resource inputs are reference-scoped while mutable areas remain Task-scoped."""
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    resource = store.reserve_resource_input(
        session_id="session-123",
        reference_id="reference-1",
        filename="report.xlsx",
    )
    task = store.activate_task(session_id="session-123", task_id="task-1")

    assert resource.relative_to(task.session_root).as_posix() == (
        "input/reference-1/report.xlsx"
    )
    assert task.work_dir.relative_to(task.session_root).as_posix() == "work/task-1"
    assert task.output_dir.relative_to(task.session_root).as_posix() == "output/task-1"
    assert task.audit_dir.relative_to(task.session_root).as_posix() == "audit/task-1"


@pytest.mark.parametrize(
    "reference_id", ["", "../evil", "reference/evil", "/absolute", "bad ref", ".", ".."]
)
def test_resource_input_rejects_invalid_reference_id(
    tmp_path: Path, reference_id: str
) -> None:
    """Reference identifiers cannot escape the Conversation input directory."""
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")

    with pytest.raises(SessionWorkspacePathError):
        store.reserve_resource_input(
            session_id="session-123",
            reference_id=reference_id,
            filename="report.xlsx",
        )


def test_resource_input_rejects_reference_directory_symlink(tmp_path: Path) -> None:
    """A Resource Reference directory cannot redirect input outside its session."""
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(session_id="session-123")
    outside = tmp_path / "outside-input"
    outside.mkdir()
    (workspace.input_dir / "reference-1").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(SessionWorkspacePathError):
        store.reserve_resource_input(
            session_id="session-123",
            reference_id="reference-1",
            filename="report.xlsx",
        )


def test_atomic_materialization_hashes_bytes_and_rejects_changed_source(
    tmp_path: Path,
) -> None:
    """The store validates the open descriptor and leaves no partial temp file."""
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    source = tmp_path / "source.txt"
    source.write_bytes(b"first version")
    stat = source.stat()
    expected = WorkspaceSourceFile(
        path=source.resolve(),
        device=stat.st_dev,
        inode=stat.st_ino,
        size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns,
    )

    materialized = store.materialize_resource_input(
        session_id="session-123",
        reference_id="reference-1",
        filename="source.txt",
        source=expected,
    )
    source.write_bytes(b"second version is different")

    assert materialized.path.read_bytes() == b"first version"
    assert materialized.content_hash == sha256(b"first version").hexdigest()
    with pytest.raises(SessionWorkspacePathError, match="changed"):
        store.materialize_resource_input(
            session_id="session-123",
            reference_id="reference-2",
            filename="source.txt",
            source=expected,
        )
    assert not list(store.root.rglob("*.tmp"))


def test_manifest_round_trip_is_bounded_and_rejects_private_or_absolute_fields(
    tmp_path: Path,
) -> None:
    """Only the fixed path-free manifest schema may cross the store boundary."""
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    payload = {
        "schema_version": "2",
        "conversation_id": "session-123",
        "resource_order": ["reference-1"],
        "resources": {
            "reference-1": {
                "version": "a" * 64,
                "content_hash": "b" * 64,
                "size_bytes": 12,
                "relative_path": "input/reference-1/report.txt",
            }
        },
        "tasks": {
            "task-1": {
                "state": "active",
                "work_dir": "work/task-1",
                "output_dir": "output/task-1",
                "audit_dir": "audit/task-1",
                "resource_ids": ["reference-1"],
                "workspace_need": True,
                "artifact_ids": [],
                "cleanup_state": "not_started",
                "cleanup_policy": "pending",
                "diagnostic": {"error_present": False},
            }
        },
    }

    store.write_manifest(
        session_id="session-123",
        payload=cast(dict[str, object], payload),
    )

    assert store.read_manifest(session_id="session-123") == payload
    poisoned = json.loads(json.dumps(payload))
    poisoned["source_ref"] = str(tmp_path / "private.txt")
    with pytest.raises(SessionWorkspacePathError):
        store.write_manifest(session_id="session-123", payload=poisoned)
    poisoned = json.loads(json.dumps(payload))
    poisoned["resources"]["reference-1"]["relative_path"] = "/private/report.txt"
    with pytest.raises(SessionWorkspacePathError):
        store.write_manifest(session_id="session-123", payload=poisoned)


def test_legacy_manifest_read_gets_bounded_lifecycle_defaults(tmp_path: Path) -> None:
    """Existing version-one manifests remain readable with safe Phase 3 defaults."""
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(session_id="session-123")
    legacy = {
        "schema_version": "1",
        "conversation_id": "session-123",
        "resource_order": [],
        "resources": {},
        "tasks": {
            "task-1": {
                "state": "success",
                "work_dir": "work/task-1",
                "output_dir": "output/task-1",
                "audit_dir": "audit/task-1",
                "resource_ids": [],
                "artifact_ids": [],
                "cleanup_state": "retaining",
                "diagnostic": {"error_present": False},
            }
        },
    }
    (workspace.root / "manifest.json").write_text(
        json.dumps(legacy),
        encoding="utf-8",
    )

    manifest = store.read_manifest(session_id="session-123")

    assert manifest is not None
    assert manifest["schema_version"] == "2"
    tasks = cast(dict[str, dict[str, object]], manifest["tasks"])
    assert tasks["task-1"]["workspace_need"] is True
    assert tasks["task-1"]["cleanup_policy"] == "success"


@pytest.mark.parametrize("raw", [b"{not-json", b"x" * (1_048_576 + 1)])
def test_manifest_read_rejects_invalid_or_oversized_bytes(
    tmp_path: Path, raw: bytes
) -> None:
    """Manifest parsing fails closed before any unvalidated field is used."""
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(session_id="session-123")
    (workspace.root / "manifest.json").write_bytes(raw)

    with pytest.raises(SessionWorkspacePathError):
        store.read_manifest(session_id="session-123")


def test_manifest_read_rejects_manifest_symlink(tmp_path: Path) -> None:
    """A symlink cannot redirect manifest reads outside the managed root."""
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(session_id="session-123")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    manifest = workspace.root / "manifest.json"
    manifest.symlink_to(outside)

    with pytest.raises(SessionWorkspacePathError):
        store.read_manifest(session_id="session-123")


def test_cleanup_rejects_task_symlink_without_touching_target(tmp_path: Path) -> None:
    """Cleanup cannot follow a Task directory symlink outside the workspace."""
    store = SessionWorkspaceStore(tmp_path / "workspace" / "sessions")
    workspace = store.create(session_id="session-123")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    task_dir = workspace.output_dir / "task-1"
    task_dir.mkdir()
    task_dir.rmdir()
    task_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SessionWorkspacePathError):
        store.cleanup_task_areas(
            session_id="session-123",
            task_id="task-1",
            areas=("output",),
        )

    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_workspace_root_symlink_is_rejected(tmp_path: Path) -> None:
    """The configured managed root itself cannot be redirected by a symlink."""
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    symlink_root = tmp_path / "symlink-root"
    symlink_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(SessionWorkspacePathError):
        SessionWorkspaceStore(symlink_root).create(session_id="session-123")
