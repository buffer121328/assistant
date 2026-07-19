from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SAFE_FILENAME = re.compile(r"^[^/\\\x00]{1,128}$")
WorkspaceArea = Literal["input", "work", "output", "audit"]
_AREAS: tuple[WorkspaceArea, ...] = ("input", "work", "output", "audit")


class SessionWorkspacePathError(ValueError):
    pass


@dataclass(frozen=True)
class SessionWorkspace:
    session_id: str
    root: Path
    input_dir: Path
    work_dir: Path
    output_dir: Path
    audit_dir: Path


class SessionWorkspaceStore:
    """Create and reserve paths inside a per-session workspace.

    A session maps to a conversation/thread. Multiple tasks in the same
    conversation intentionally share this workspace so follow-up messages can use
    prior session material without exposing other conversations. The sandbox
    provider directory remains separate and is only for high-risk execution.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def create(self, *, session_id: str) -> SessionWorkspace:
        safe_session_id = self._safe_session_id(session_id)
        session_root = self.root / safe_session_id
        if session_root.is_symlink():
            raise SessionWorkspacePathError("Session workspace root must not be a symlink")
        session_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._assert_child(session_root, expected_parent=self.root)

        area_dirs: dict[WorkspaceArea, Path] = {}
        for area in _AREAS:
            area_dir = session_root / area
            if area_dir.is_symlink():
                raise SessionWorkspacePathError("Session workspace area must not be a symlink")
            area_dir.mkdir(mode=0o700, exist_ok=True)
            self._assert_child(area_dir, expected_parent=session_root)
            area_dirs[area] = area_dir.resolve(strict=True)

        return SessionWorkspace(
            session_id=safe_session_id,
            root=session_root.resolve(strict=True),
            input_dir=area_dirs["input"],
            work_dir=area_dirs["work"],
            output_dir=area_dirs["output"],
            audit_dir=area_dirs["audit"],
        )

    def path_for(self, *, session_id: str, area: WorkspaceArea, filename: str) -> Path:
        safe_filename = self._safe_filename(filename)
        workspace = self.create(session_id=session_id)
        area_dir = self._area_dir(workspace, area)
        target = area_dir / safe_filename
        self._assert_target(target, expected_parent=area_dir)
        return target

    def reserve_input(self, *, session_id: str, filename: str) -> Path:
        return self.path_for(session_id=session_id, area="input", filename=filename)

    def reserve_work(self, *, session_id: str, filename: str) -> Path:
        return self.path_for(session_id=session_id, area="work", filename=filename)

    def reserve_output(self, *, session_id: str, filename: str) -> Path:
        return self.path_for(session_id=session_id, area="output", filename=filename)

    def reserve_audit(self, *, session_id: str, filename: str) -> Path:
        return self.path_for(session_id=session_id, area="audit", filename=filename)

    def _safe_session_id(self, session_id: str) -> str:
        safe_session_id = session_id.strip()
        if not _SAFE_SESSION_ID.fullmatch(safe_session_id):
            raise SessionWorkspacePathError("Invalid session id")
        return safe_session_id

    def _safe_filename(self, filename: str) -> str:
        safe_filename = filename.strip()
        path = Path(safe_filename)
        if (
            not _SAFE_FILENAME.fullmatch(safe_filename)
            or path.is_absolute()
            or path.name != safe_filename
            or safe_filename in {".", ".."}
        ):
            raise SessionWorkspacePathError("Invalid workspace filename")
        return safe_filename

    def _area_dir(self, workspace: SessionWorkspace, area: WorkspaceArea) -> Path:
        if area == "input":
            return workspace.input_dir
        if area == "work":
            return workspace.work_dir
        if area == "output":
            return workspace.output_dir
        if area == "audit":
            return workspace.audit_dir
        raise SessionWorkspacePathError("Invalid workspace area")

    def _assert_child(self, path: Path, *, expected_parent: Path) -> None:
        resolved = path.resolve(strict=True)
        if resolved.parent != expected_parent.resolve(strict=True):
            raise SessionWorkspacePathError("Workspace path escaped root")

    def _assert_target(self, target: Path, *, expected_parent: Path) -> None:
        if target.is_symlink():
            raise SessionWorkspacePathError("Workspace target must not be a symlink")
        resolved_parent = target.parent.resolve(strict=True)
        if resolved_parent != expected_parent.resolve(strict=True):
            raise SessionWorkspacePathError("Workspace target escaped area")
        resolved_target = target.resolve(strict=False)
        if resolved_target.parent != resolved_parent:
            raise SessionWorkspacePathError("Workspace target escaped area")


class TaskWorkspaceStore:
    """Backward-compatible name for callers not yet migrated to session wording."""

    def __init__(self, root: Path) -> None:
        self._store = SessionWorkspaceStore(root)

    def create(self, *, task_id: str) -> SessionWorkspace:
        return self._store.create(session_id=task_id)

    def path_for(self, *, task_id: str, area: WorkspaceArea, filename: str) -> Path:
        return self._store.path_for(session_id=task_id, area=area, filename=filename)

    def reserve_input(self, *, task_id: str, filename: str) -> Path:
        return self._store.reserve_input(session_id=task_id, filename=filename)

    def reserve_work(self, *, task_id: str, filename: str) -> Path:
        return self._store.reserve_work(session_id=task_id, filename=filename)

    def reserve_output(self, *, task_id: str, filename: str) -> Path:
        return self._store.reserve_output(session_id=task_id, filename=filename)

    def reserve_audit(self, *, task_id: str, filename: str) -> Path:
        return self._store.reserve_audit(session_id=task_id, filename=filename)


TaskWorkspace = SessionWorkspace
TaskWorkspacePathError = SessionWorkspacePathError

__all__ = [
    "SessionWorkspace",
    "SessionWorkspacePathError",
    "SessionWorkspaceStore",
    "TaskWorkspace",
    "TaskWorkspacePathError",
    "TaskWorkspaceStore",
]
