from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat as stat_module
import tempfile
from typing import Literal

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SAFE_TASK_ID = _SAFE_SESSION_ID
_SAFE_REFERENCE_ID = _SAFE_SESSION_ID
_SAFE_FILENAME = re.compile(r"^[^/\\\x00]{1,128}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_MANIFEST_ITEMS = 256
_COPY_CHUNK_BYTES = 1024 * 1024
_MANIFEST_SCHEMA_VERSION = "2"
_LEGACY_MANIFEST_SCHEMA_VERSION = "1"
WorkspaceArea = Literal["input", "work", "output", "audit"]
TaskScopedWorkspaceArea = Literal["work", "output", "audit"]
_AREAS: tuple[WorkspaceArea, ...] = ("input", "work", "output", "audit")


class SessionWorkspacePathError(ValueError):
    """表示 处理 session workspace path error 的后端数据结构或服务对象。"""

    pass


class WorkspaceSourceChangedError(SessionWorkspacePathError):
    """定义当前组件可安全处理的错误类型。"""


@dataclass(frozen=True)
class SessionWorkspace:
    """表示 处理 session workspace 的后端数据结构或服务对象。"""

    session_id: str  # session_id 对应的数据字段。
    root: Path  # root 对应的数据字段。
    input_dir: Path  # input_dir 对应的数据字段。
    work_dir: Path  # work_dir 对应的数据字段。
    output_dir: Path  # output_dir 对应的数据字段。
    audit_dir: Path  # audit_dir 对应的数据字段。


@dataclass(frozen=True)
class TaskWorkspace:
    """定义当前组件的职责和边界。"""

    session_id: str  # session_id 对应的数据字段。
    task_id: str  # task_id 对应的数据字段。
    session_root: Path  # session_root 对应的数据字段。
    work_dir: Path  # work_dir 对应的数据字段。
    output_dir: Path  # output_dir 对应的数据字段。
    audit_dir: Path  # audit_dir 对应的数据字段。


@dataclass(frozen=True)
class WorkspaceSourceFile:
    """定义当前组件的职责和边界。"""

    path: Path  # path 对应的数据字段。
    device: int  # device 对应的数据字段。
    inode: int  # inode 对应的数据字段。
    size_bytes: int  # size_bytes 对应的数据字段。
    modified_ns: int  # modified_ns 对应的数据字段。


@dataclass(frozen=True)
class MaterializedWorkspaceInput:
    """定义当前组件的职责和边界。"""

    path: Path  # path 对应的数据字段。
    relative_path: str  # relative_path 对应的数据字段。
    content_hash: str  # content_hash 对应的数据字段。
    size_bytes: int  # size_bytes 对应的数据字段。


class SessionWorkspaceStore:
    """定义当前组件的职责和边界。"""

    def __init__(self, root: Path) -> None:
        """初始化对象实例。

        Args:
            root: root 参数。
        """
        # Keep the configured root itself visible to the symlink checks. Child
        # containment is resolved separately after each backend-created path.
        self.root = root.expanduser().absolute()

    def create(self, *, session_id: str) -> SessionWorkspace:
        """创建。

        Args:
            session_id: session_id 参数。
        """
        safe_session_id = self._safe_session_id(session_id)
        if self.root.is_symlink():
            raise SessionWorkspacePathError("Workspace root must not be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
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
        """处理 reserve input。

        Args:
            session_id: session_id 参数。
            filename: filename 参数。
        """
        safe_filename = self._safe_filename(filename)
        workspace = self.create(session_id=session_id)
        target = workspace.input_dir / safe_filename
        self._assert_target(target, expected_parent=workspace.input_dir)
        return target

    def reserve_resource_input(
        self, *, session_id: str, reference_id: str, filename: str
    ) -> Path:
        """Reserve a file below a validated Resource Reference input directory.

        Args:
            session_id: 用于执行当前操作的 session id 参数。
            reference_id: 用于执行当前操作的 reference id 参数。
            filename: 用于执行当前操作的 filename 参数。
        """
        safe_reference_id = self._safe_reference_id(reference_id)
        safe_filename = self._safe_filename(filename)
        workspace = self.create(session_id=session_id)
        reference_dir = workspace.input_dir / safe_reference_id
        if reference_dir.is_symlink():
            raise SessionWorkspacePathError(
                "Resource workspace directory must not be a symlink"
            )
        reference_dir.mkdir(mode=0o700, exist_ok=True)
        self._assert_child(reference_dir, expected_parent=workspace.input_dir)
        resolved_reference_dir = reference_dir.resolve(strict=True)
        target = resolved_reference_dir / safe_filename
        self._assert_target(target, expected_parent=resolved_reference_dir)
        return target

    def activate_task(self, *, session_id: str, task_id: str) -> TaskWorkspace:
        """Create the existing Task-scoped work, output, and audit directories.

        Args:
            session_id: 用于执行当前操作的 session id 参数。
            task_id: 目标任务 ID。
        """
        safe_task_id = self._safe_task_id(task_id)
        workspace = self.create(session_id=session_id)
        return TaskWorkspace(
            session_id=workspace.session_id,
            task_id=safe_task_id,
            session_root=workspace.root,
            work_dir=self._task_dir(workspace, safe_task_id, "work"),
            output_dir=self._task_dir(workspace, safe_task_id, "output"),
            audit_dir=self._task_dir(workspace, safe_task_id, "audit"),
        )

    def materialize_resource_input(
        self,
        *,
        session_id: str,
        reference_id: str,
        filename: str,
        source: WorkspaceSourceFile,
    ) -> MaterializedWorkspaceInput:
        """Stream one authorized regular file into its atomic workspace target.

        Args:
            session_id: 用于执行当前操作的 session id 参数。
            reference_id: 用于执行当前操作的 reference id 参数。
            filename: 用于执行当前操作的 filename 参数。
            source: 用于执行当前操作的 source 参数。
        """
        target = self.reserve_resource_input(
            session_id=session_id,
            reference_id=reference_id,
            filename=filename,
        )
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            source_descriptor = os.open(source.path, flags)
        except OSError as exc:
            raise WorkspaceSourceChangedError(
                "Workspace source is unavailable"
            ) from exc
        temporary: Path | None = None
        try:
            before = os.fstat(source_descriptor)
            self._assert_source_identity(before, source)
            temporary_descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
            )
            temporary = Path(temporary_name)
            digest = sha256()
            copied = 0
            with (
                os.fdopen(source_descriptor, "rb", closefd=False) as source_stream,
                os.fdopen(temporary_descriptor, "wb") as target_stream,
            ):
                while chunk := source_stream.read(_COPY_CHUNK_BYTES):
                    target_stream.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            after = os.fstat(source_descriptor)
            self._assert_source_identity(after, source)
            if copied != source.size_bytes:
                raise WorkspaceSourceChangedError(
                    "Workspace source changed during copy"
                )
            self._assert_target(target, expected_parent=target.parent)
            os.replace(temporary, target)
            temporary = None
            workspace = self.create(session_id=session_id)
            return MaterializedWorkspaceInput(
                path=target,
                relative_path=target.relative_to(workspace.root).as_posix(),
                content_hash=digest.hexdigest(),
                size_bytes=copied,
            )
        except SessionWorkspacePathError:
            raise
        except OSError as exc:
            raise SessionWorkspacePathError(
                "Workspace materialization failed"
            ) from exc
        finally:
            os.close(source_descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def cleanup_task_areas(
        self,
        *,
        session_id: str,
        task_id: str,
        areas: tuple[TaskScopedWorkspaceArea, ...],
    ) -> tuple[TaskScopedWorkspaceArea, ...]:
        """Remove only existing validated Task directories without creating a workspace.

        Args:
            session_id: 用于执行当前操作的 session id 参数。
            task_id: 目标任务 ID。
            areas: 用于执行当前操作的 areas 参数。
        """
        safe_session_id = self._safe_session_id(session_id)
        safe_task_id = self._safe_task_id(task_id)
        if not areas or len(set(areas)) != len(areas):
            raise SessionWorkspacePathError("Task cleanup areas are invalid")
        if any(area not in {"work", "output", "audit"} for area in areas):
            raise SessionWorkspacePathError("Task cleanup areas are invalid")
        session_root = self._existing_session_root(safe_session_id)
        if session_root is None:
            return ()

        removed: list[TaskScopedWorkspaceArea] = []
        for area in areas:
            area_dir = session_root / area
            if area_dir.is_symlink() or not area_dir.is_dir():
                raise SessionWorkspacePathError("Session workspace area is invalid")
            self._assert_child(area_dir, expected_parent=session_root)
            task_dir = area_dir / safe_task_id
            if task_dir.is_symlink():
                raise SessionWorkspacePathError(
                    "Task workspace directory must not be a symlink"
                )
            if not task_dir.exists():
                continue
            if not task_dir.is_dir():
                raise SessionWorkspacePathError("Task workspace directory is invalid")
            self._assert_child(task_dir, expected_parent=area_dir)
            self._remove_tree(task_dir, expected_parent=area_dir)
            removed.append(area)
        return tuple(removed)

    def manifest_exists(self, *, session_id: str) -> bool:
        """Return whether a validated session already has a regular manifest file.

        Args:
            session_id: 用于执行当前操作的 session id 参数。
        """
        safe_session_id = self._safe_session_id(session_id)
        if self.root.is_symlink():
            raise SessionWorkspacePathError("Workspace root is invalid")
        if not self.root.exists():
            return False
        if not self.root.is_dir():
            raise SessionWorkspacePathError("Workspace root is invalid")
        session_root = self.root / safe_session_id
        if session_root.is_symlink():
            raise SessionWorkspacePathError("Session workspace root is invalid")
        if not session_root.exists():
            return False
        if not session_root.is_dir():
            raise SessionWorkspacePathError("Session workspace root is invalid")
        self._assert_child(session_root, expected_parent=self.root)
        target = session_root / "manifest.json"
        if target.is_symlink():
            raise SessionWorkspacePathError("Workspace manifest must not be a symlink")
        return target.is_file()

    def read_manifest(self, *, session_id: str) -> dict[str, object] | None:
        """Read and validate one bounded path-free workspace manifest.

        Args:
            session_id: 用于执行当前操作的 session id 参数。
        """
        if not self.manifest_exists(session_id=session_id):
            return None
        safe_session_id = self._safe_session_id(session_id)
        target = self.root / safe_session_id / "manifest.json"
        try:
            raw = target.read_bytes()
            if len(raw) > _MAX_MANIFEST_BYTES:
                raise SessionWorkspacePathError("Workspace manifest is too large")
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SessionWorkspacePathError("Workspace manifest is invalid") from exc
        if not isinstance(payload, dict):
            raise SessionWorkspacePathError("Workspace manifest is invalid")
        normalized = self._normalize_manifest(payload)
        self._validate_manifest(normalized, session_id=safe_session_id)
        return normalized

    def write_manifest(
        self, *, session_id: str, payload: dict[str, object]
    ) -> Path:
        """Validate and atomically replace a bounded workspace manifest.

        Args:
            session_id: 用于执行当前操作的 session id 参数。
            payload: 当前操作的结构化载荷。
        """
        safe_session_id = self._safe_session_id(session_id)
        normalized = self._normalize_manifest(payload)
        self._validate_manifest(normalized, session_id=safe_session_id)
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_MANIFEST_BYTES:
            raise SessionWorkspacePathError("Workspace manifest is too large")
        workspace = self.create(session_id=safe_session_id)
        target = workspace.root / "manifest.json"
        self._assert_target(target, expected_parent=workspace.root)
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=workspace.root,
                prefix=".manifest.",
                suffix=".tmp",
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise SessionWorkspacePathError("Workspace manifest write failed") from exc
        return target

    def _reserve_task_file(
        self,
        *,
        session_id: str,
        task_id: str,
        area: TaskScopedWorkspaceArea,
        filename: str,
    ) -> Path:
        """执行 处理 reserve task file 的内部辅助逻辑。

        Args:
            session_id: session_id 参数。
            task_id: task_id 参数。
            area: area 参数。
            filename: filename 参数。
        """
        safe_task_id = self._safe_task_id(task_id)
        safe_filename = self._safe_filename(filename)
        workspace = self.create(session_id=session_id)
        resolved_task_dir = self._task_dir(workspace, safe_task_id, area)
        target = resolved_task_dir / safe_filename
        self._assert_target(target, expected_parent=resolved_task_dir)
        return target

    def reserve_task_work(
        self, *, session_id: str, task_id: str, filename: str
    ) -> Path:
        """处理 reserve task work。

        Args:
            session_id: session_id 参数。
            task_id: task_id 参数。
            filename: filename 参数。
        """
        return self._reserve_task_file(
            session_id=session_id, task_id=task_id, area="work", filename=filename
        )

    def reserve_task_output(
        self, *, session_id: str, task_id: str, filename: str
    ) -> Path:
        """处理 reserve task output。

        Args:
            session_id: session_id 参数。
            task_id: task_id 参数。
            filename: filename 参数。
        """
        return self._reserve_task_file(
            session_id=session_id, task_id=task_id, area="output", filename=filename
        )

    def reserve_task_audit(
        self, *, session_id: str, task_id: str, filename: str
    ) -> Path:
        """处理 reserve task audit。

        Args:
            session_id: session_id 参数。
            task_id: task_id 参数。
            filename: filename 参数。
        """
        return self._reserve_task_file(
            session_id=session_id, task_id=task_id, area="audit", filename=filename
        )

    def _safe_session_id(self, session_id: str) -> str:
        """执行 处理 safe session id 的内部辅助逻辑。

        Args:
            session_id: session_id 参数。
        """
        safe_session_id = session_id.strip()
        if not _SAFE_SESSION_ID.fullmatch(safe_session_id):
            raise SessionWorkspacePathError("Invalid session id")
        return safe_session_id

    def _safe_task_id(self, task_id: str) -> str:
        """执行 处理 safe task id 的内部辅助逻辑。

        Args:
            task_id: task_id 参数。
        """
        safe_task_id = task_id.strip()
        if not _SAFE_TASK_ID.fullmatch(safe_task_id):
            raise SessionWorkspacePathError("Invalid task id")
        return safe_task_id

    def _safe_reference_id(self, reference_id: str) -> str:
        """Validate a Resource Reference component before input path creation.

        Args:
            reference_id: 用于执行当前操作的 reference id 参数。
        """
        safe_reference_id = reference_id.strip()
        if not _SAFE_REFERENCE_ID.fullmatch(safe_reference_id):
            raise SessionWorkspacePathError("Invalid resource reference id")
        return safe_reference_id

    def _safe_filename(self, filename: str) -> str:
        """执行 处理 safe filename 的内部辅助逻辑。

        Args:
            filename: filename 参数。
        """
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
        """执行 处理 task area dir 的内部辅助逻辑。

        Args:
            workspace: workspace 参数。
            area: area 参数。
        """
        if area == "work":
            return workspace.work_dir
        if area == "output":
            return workspace.output_dir
        if area == "audit":
            return workspace.audit_dir
        raise SessionWorkspacePathError("Invalid task workspace area")

    def _task_dir(
        self,
        workspace: SessionWorkspace,
        task_id: str,
        area: TaskScopedWorkspaceArea,
    ) -> Path:
        """Create one validated Task directory under a mutable workspace area.

        Args:
            workspace: 用于执行当前操作的 workspace 参数。
            task_id: 目标任务 ID。
            area: 用于执行当前操作的 area 参数。
        """
        area_dir = self._task_area_dir(workspace, area)
        task_dir = area_dir / task_id
        if task_dir.is_symlink():
            raise SessionWorkspacePathError(
                "Task workspace directory must not be a symlink"
            )
        task_dir.mkdir(mode=0o700, exist_ok=True)
        self._assert_child(task_dir, expected_parent=area_dir)
        return task_dir.resolve(strict=True)

    def _existing_session_root(self, session_id: str) -> Path | None:
        """Resolve one already-created session root without creating any path.

        Args:
            session_id: 用于执行当前操作的 session id 参数。
        """
        if self.root.is_symlink():
            raise SessionWorkspacePathError("Workspace root is invalid")
        if not self.root.exists():
            return None
        if not self.root.is_dir():
            raise SessionWorkspacePathError("Workspace root is invalid")
        session_root = self.root / session_id
        if session_root.is_symlink():
            raise SessionWorkspacePathError("Session workspace root is invalid")
        if not session_root.exists():
            return None
        if not session_root.is_dir():
            raise SessionWorkspacePathError("Session workspace root is invalid")
        self._assert_child(session_root, expected_parent=self.root)
        return session_root.resolve(strict=True)

    def _remove_tree(self, path: Path, *, expected_parent: Path) -> None:
        """Remove a validated non-symlink directory tree under one Task area.

        Args:
            path: 用于执行当前操作的 path 参数。
            expected_parent: 用于执行当前操作的 expected parent 参数。
        """
        if path.is_symlink() or not path.is_dir():
            raise SessionWorkspacePathError("Task workspace directory is invalid")
        self._assert_child(path, expected_parent=expected_parent)
        entries = tuple(path.iterdir())
        for entry in entries:
            if entry.is_symlink():
                raise SessionWorkspacePathError(
                    "Task workspace content must not be a symlink"
                )
            self._assert_child(entry, expected_parent=path)
            if entry.is_dir():
                self._remove_tree(entry, expected_parent=path)
            elif entry.is_file():
                entry.unlink()
            else:
                raise SessionWorkspacePathError("Task workspace content is invalid")
        path.rmdir()

    @staticmethod
    def _assert_source_identity(
        current: os.stat_result, expected: WorkspaceSourceFile
    ) -> None:
        """Require an open regular file descriptor to match resolved metadata.

        Args:
            current: 用于执行当前操作的 current 参数。
            expected: 用于执行当前操作的 expected 参数。
        """
        if not stat_module.S_ISREG(current.st_mode):
            raise WorkspaceSourceChangedError(
                "Workspace source must be a regular file"
            )
        if (
            current.st_dev != expected.device
            or current.st_ino != expected.inode
            or current.st_size != expected.size_bytes
            or current.st_mtime_ns != expected.modified_ns
        ):
            raise WorkspaceSourceChangedError("Workspace source changed before copy")

    def _validate_manifest(
        self, payload: dict[str, object], *, session_id: str
    ) -> None:
        """Reject extra, private, unbounded, or path-escaping manifest fields.

        Args:
            payload: 当前操作的结构化载荷。
            session_id: 用于执行当前操作的 session id 参数。
        """
        if set(payload) != {
            "schema_version",
            "conversation_id",
            "resource_order",
            "resources",
            "tasks",
        }:
            raise SessionWorkspacePathError("Workspace manifest fields are invalid")
        if (
            payload["schema_version"] != _MANIFEST_SCHEMA_VERSION
            or payload["conversation_id"] != session_id
        ):
            raise SessionWorkspacePathError("Workspace manifest identity is invalid")
        resource_order = payload["resource_order"]
        resources = payload["resources"]
        tasks = payload["tasks"]
        if (
            not isinstance(resource_order, list)
            or not isinstance(resources, dict)
            or not isinstance(tasks, dict)
            or len(resource_order) > _MAX_MANIFEST_ITEMS
            or len(resources) > _MAX_MANIFEST_ITEMS
            or len(tasks) > _MAX_MANIFEST_ITEMS
        ):
            raise SessionWorkspacePathError("Workspace manifest collections are invalid")
        if (
            any(
                not isinstance(reference_id, str)
                or not _SAFE_REFERENCE_ID.fullmatch(reference_id)
                for reference_id in resource_order
            )
            or len(set(resource_order)) != len(resource_order)
            or set(resource_order) != set(resources)
        ):
            raise SessionWorkspacePathError("Workspace manifest resource order is invalid")
        for reference_id, item in resources.items():
            if not isinstance(reference_id, str):
                raise SessionWorkspacePathError("Workspace resource id is invalid")
            safe_reference_id = self._safe_reference_id(reference_id)
            if not isinstance(item, dict) or set(item) != {
                "version",
                "content_hash",
                "size_bytes",
                "relative_path",
            }:
                raise SessionWorkspacePathError("Workspace resource manifest is invalid")
            version = item["version"]
            content_hash = item["content_hash"]
            size_bytes = item["size_bytes"]
            relative_path = item["relative_path"]
            if (
                not isinstance(version, str)
                or not _HEX_64.fullmatch(version)
                or not isinstance(content_hash, str)
                or not _HEX_64.fullmatch(content_hash)
                or not isinstance(size_bytes, int)
                or isinstance(size_bytes, bool)
                or size_bytes < 0
                or not isinstance(relative_path, str)
            ):
                raise SessionWorkspacePathError("Workspace resource metadata is invalid")
            parts = Path(relative_path).parts
            if (
                Path(relative_path).is_absolute()
                or len(parts) != 3
                or parts[0] != "input"
                or parts[1] != safe_reference_id
                or self._safe_filename(parts[2]) != parts[2]
            ):
                raise SessionWorkspacePathError("Workspace resource path is invalid")
        for task_id, item in tasks.items():
            if not isinstance(task_id, str):
                raise SessionWorkspacePathError("Workspace task id is invalid")
            safe_task_id = self._safe_task_id(task_id)
            if not isinstance(item, dict) or set(item) != {
                "state",
                "work_dir",
                "output_dir",
                "audit_dir",
                "resource_ids",
                "workspace_need",
                "artifact_ids",
                "cleanup_state",
                "cleanup_policy",
                "diagnostic",
            }:
                raise SessionWorkspacePathError("Workspace task manifest is invalid")
            if item["state"] not in {"active", "success", "failed", "cancelled"}:
                raise SessionWorkspacePathError("Workspace task state is invalid")
            for field, area in (
                ("work_dir", "work"),
                ("output_dir", "output"),
                ("audit_dir", "audit"),
            ):
                if item[field] != f"{area}/{safe_task_id}":
                    raise SessionWorkspacePathError("Workspace task path is invalid")
            artifact_ids = item["artifact_ids"]
            resource_ids = item["resource_ids"]
            workspace_need = item["workspace_need"]
            if (
                not isinstance(resource_ids, list)
                or len(resource_ids) > _MAX_MANIFEST_ITEMS
                or any(
                    not isinstance(value, str)
                    or value not in resources
                    for value in resource_ids
                )
                or len(set(resource_ids)) != len(resource_ids)
            ):
                raise SessionWorkspacePathError("Workspace task resources are invalid")
            if not isinstance(workspace_need, bool) or not workspace_need:
                raise SessionWorkspacePathError("Workspace task need is invalid")
            if (
                not isinstance(artifact_ids, list)
                or len(artifact_ids) > _MAX_MANIFEST_ITEMS
                or any(
                    not isinstance(value, str)
                    or not _SAFE_SESSION_ID.fullmatch(value)
                    for value in artifact_ids
                )
                or len(set(artifact_ids)) != len(artifact_ids)
            ):
                raise SessionWorkspacePathError("Workspace artifact ids are invalid")
            if item["cleanup_state"] not in {
                "not_started",
                "retaining",
                "cleaned",
                "diagnostic_retained",
            }:
                raise SessionWorkspacePathError("Workspace cleanup state is invalid")
            if item["cleanup_policy"] not in {
                "pending",
                "success",
                "failed",
                "diagnostic_retained",
            }:
                raise SessionWorkspacePathError("Workspace cleanup policy is invalid")
            diagnostic = item["diagnostic"]
            if (
                not isinstance(diagnostic, dict)
                or set(diagnostic) != {"error_present"}
                or not isinstance(diagnostic["error_present"], bool)
            ):
                raise SessionWorkspacePathError("Workspace diagnostic is invalid")

    @staticmethod
    def _normalize_manifest(payload: dict[str, object]) -> dict[str, object]:
        """Upgrade a version-one path-safe manifest to bounded lifecycle facts.

        Args:
            payload: 当前操作的结构化载荷。
        """
        if payload.get("schema_version") != _LEGACY_MANIFEST_SCHEMA_VERSION:
            return payload
        if set(payload) != {
            "schema_version",
            "conversation_id",
            "resource_order",
            "resources",
            "tasks",
        }:
            return payload
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, dict):
            return payload
        normalized_tasks: dict[object, object] = {}
        for task_id, raw_item in raw_tasks.items():
            if not isinstance(raw_item, dict):
                normalized_tasks[task_id] = raw_item
                continue
            state = raw_item.get("state")
            cleanup_policy = (
                "success"
                if state == "success"
                else "failed"
                if state in {"failed", "cancelled"}
                else "pending"
            )
            normalized_tasks[task_id] = {
                **raw_item,
                "workspace_need": True,
                "cleanup_policy": cleanup_policy,
            }
        return {
            **payload,
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "tasks": normalized_tasks,
        }

    def _assert_child(self, path: Path, *, expected_parent: Path) -> None:
        """执行 处理 assert child 的内部辅助逻辑。

        Args:
            path: path 参数。
            expected_parent: expected_parent 参数。
        """
        resolved = path.resolve(strict=True)
        if resolved.parent != expected_parent.resolve(strict=True):
            raise SessionWorkspacePathError("Workspace path escaped root")

    def _assert_target(self, target: Path, *, expected_parent: Path) -> None:
        """执行 处理 assert target 的内部辅助逻辑。

        Args:
            target: target 参数。
            expected_parent: expected_parent 参数。
        """
        if target.is_symlink():
            raise SessionWorkspacePathError("Workspace target must not be a symlink")
        resolved_parent = target.parent.resolve(strict=True)
        if resolved_parent != expected_parent.resolve(strict=True):
            raise SessionWorkspacePathError("Workspace target escaped area")
        resolved_target = target.resolve(strict=False)
        if resolved_target.parent != resolved_parent:
            raise SessionWorkspacePathError("Workspace target escaped area")


__all__ = [
    "MaterializedWorkspaceInput",
    "SessionWorkspace",
    "SessionWorkspacePathError",
    "SessionWorkspaceStore",
    "TaskWorkspace",
    "WorkspaceSourceChangedError",
    "WorkspaceSourceFile",
]
