from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SAFE_TASK_ID = _SAFE_SESSION_ID
_SAFE_FILENAME = re.compile(r"^[^/\\\x00]{1,128}$")
WorkspaceArea = Literal["input", "work", "output", "audit"]
TaskScopedWorkspaceArea = Literal["work", "output", "audit"]
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
    conversation intentionally share the session root. Input files are shared at
    session level, while work/output/audit files must be reserved under a task id
    subdirectory so concurrent or follow-up tasks cannot collide on flat names.
    The sandbox provider directory remains separate and is only for high-risk
    execution.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def create(self, *, session_id: str) -> SessionWorkspace:
        safe_session_id = self._safe_session_id(session_id)
        session_root = self.root / safe_session_id
        if session_root.is_symlink():
            raise SessionWorkspacePathError(
                "Session workspace root must not be a symlink"
            )
        session_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._assert_child(session_root, expected_parent=self.root)

        area_dirs: dict[WorkspaceArea, Path] = {}
        for area in _AREAS:
            area_dir = session_root / area
            if area_dir.is_symlink():
                raise SessionWorkspacePathError(
                    "Session workspace area must not be a symlink"
                )
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

    def reserve_input(self, *, session_id: str, filename: str) -> Path:
        safe_filename = self._safe_filename(filename)
        workspace = self.create(session_id=session_id)
        target = workspace.input_dir / safe_filename
        self._assert_target(target, expected_parent=workspace.input_dir)
        return target

    def _reserve_task_file(
        self,
        *,
        session_id: str,
        task_id: str,
        area: TaskScopedWorkspaceArea,
        filename: str,
    ) -> Path:
        safe_task_id = self._safe_task_id(task_id)
        safe_filename = self._safe_filename(filename)
        workspace = self.create(session_id=session_id)
        area_dir = self._task_area_dir(workspace, area)
        task_dir = area_dir / safe_task_id
        if task_dir.is_symlink():
            raise SessionWorkspacePathError(
                "Task workspace directory must not be a symlink"
            )
        task_dir.mkdir(mode=0o700, exist_ok=True)
        self._assert_child(task_dir, expected_parent=area_dir)
        resolved_task_dir = task_dir.resolve(strict=True)
        target = resolved_task_dir / safe_filename
        self._assert_target(target, expected_parent=resolved_task_dir)
        return target

    def reserve_task_work(
        self, *, session_id: str, task_id: str, filename: str
    ) -> Path:
        return self._reserve_task_file(
            session_id=session_id, task_id=task_id, area="work", filename=filename
        )

    def reserve_task_output(
        self, *, session_id: str, task_id: str, filename: str
    ) -> Path:
        return self._reserve_task_file(
            session_id=session_id, task_id=task_id, area="output", filename=filename
        )

    def reserve_task_audit(
        self, *, session_id: str, task_id: str, filename: str
    ) -> Path:
        return self._reserve_task_file(
            session_id=session_id, task_id=task_id, area="audit", filename=filename
        )

    def _safe_session_id(self, session_id: str) -> str:
        safe_session_id = session_id.strip()
        if not _SAFE_SESSION_ID.fullmatch(safe_session_id):
            raise SessionWorkspacePathError("Invalid session id")
        return safe_session_id

    def _safe_task_id(self, task_id: str) -> str:
        safe_task_id = task_id.strip()
        if not _SAFE_TASK_ID.fullmatch(safe_task_id):
            raise SessionWorkspacePathError("Invalid task id")
        return safe_task_id

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

    def _task_area_dir(
        self, workspace: SessionWorkspace, area: TaskScopedWorkspaceArea
    ) -> Path:
        if area == "work":
            return workspace.work_dir
        if area == "output":
            return workspace.output_dir
        if area == "audit":
            return workspace.audit_dir
        raise SessionWorkspacePathError("Invalid task workspace area")

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


__all__ = [
    "SessionWorkspace",
    "SessionWorkspacePathError",
    "SessionWorkspaceStore",
]
