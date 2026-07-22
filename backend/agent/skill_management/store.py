from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

from agent.skill_management.loader import (
    ALLOWED_SKILL_RESOURCE_DIRS,
    MAX_SKILL_RESOURCE_BYTES,
    SkillDefinition,
    strip_skill_frontmatter,
)


_SAFE_SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_SAFE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$")
_PACKAGE_KEYS = {
    "schema_version",
    "name",
    "display_name",
    "summary",
    "version",
}
_STORED_KEYS = _PACKAGE_KEYS | {"enabled"}

MAX_ARCHIVE_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024
MAX_SKILL_BYTES = 128 * 1024
MAX_DISPLAY_NAME_CHARS = 120
MAX_SUMMARY_CHARS = 500


class ManagedSkillStoreError(ValueError):
    """表示 处理 managed skill store error 的后端数据结构或服务对象。"""

    code = "skill_store_error"
    status_code = 400


class InvalidManagedSkillError(ManagedSkillStoreError):
    """表示 处理 invalid managed skill error 的后端数据结构或服务对象。"""

    code = "invalid_skill"


class InvalidSkillPackageError(ManagedSkillStoreError):
    """表示 处理 invalid skill package error 的后端数据结构或服务对象。"""

    code = "invalid_skill_package"


class ManagedSkillConflictError(ManagedSkillStoreError):
    """表示 处理 managed skill conflict error 的后端数据结构或服务对象。"""

    code = "skill_conflict"
    status_code = 409


class ManagedSkillNotFoundError(ManagedSkillStoreError):
    """表示 处理 managed skill not found error 的后端数据结构或服务对象。"""

    code = "skill_not_found"
    status_code = 404


class ManagedSkillImmutableError(ManagedSkillStoreError):
    """表示 处理 managed skill immutable error 的后端数据结构或服务对象。"""

    code = "skill_immutable"
    status_code = 409


@dataclass(frozen=True)
class ManagedSkillRecord:
    """表示 处理 managed skill record 的后端数据结构或服务对象。"""

    schema_version: int
    name: str
    display_name: str
    summary: str
    version: str
    enabled: bool
    directory: Path
    source: str = "managed"


@dataclass(frozen=True)
class _SkillManifest:
    """表示 处理 skill manifest 的后端数据结构或服务对象。"""

    schema_version: int
    name: str
    display_name: str
    summary: str
    version: str
    enabled: bool


class ManagedSkillStore:
    """表示 处理 managed skill store 的后端数据结构或服务对象。"""

    def __init__(self, *, builtin_root: Path, managed_root: Path) -> None:
        """初始化对象实例。

        Args:
            builtin_root: builtin_root 参数。
            managed_root: managed_root 参数。
        """
        self.builtin_root = builtin_root.expanduser().resolve()
        self.managed_root = managed_root.expanduser().resolve()

    def list_managed(self) -> tuple[ManagedSkillRecord, ...]:
        """列出 managed。"""
        if not self.managed_root.exists():
            return ()
        if not self.managed_root.is_dir():
            return ()

        records: list[ManagedSkillRecord] = []
        for directory in sorted(
            self.managed_root.iterdir(), key=lambda item: item.name
        ):
            record = self._read_record(directory)
            if record is not None:
                records.append(record)
        return tuple(records)

    def get(self, name: str) -> ManagedSkillRecord:
        """获取。

        Args:
            name: name 参数。
        """
        self._validate_name(name)
        record = self._read_record(self.managed_root / name)
        if record is None:
            if self._builtin_exists(name):
                raise ManagedSkillImmutableError(f"Built-in Skill is immutable: {name}")
            raise ManagedSkillNotFoundError(f"Managed Skill not found: {name}")
        return record

    def create(
        self,
        *,
        name: str,
        display_name: str,
        summary: str,
        instructions: str,
    ) -> ManagedSkillRecord:
        """创建。

        Args:
            name: name 参数。
            display_name: display_name 参数。
            summary: summary 参数。
            instructions: instructions 参数。
        """
        manifest = self._validated_manifest(
            {
                "schema_version": 1,
                "name": name,
                "display_name": display_name,
                "summary": summary,
                "version": "1.0.0",
            },
            stored=False,
            error_type=InvalidManagedSkillError,
        )
        normalized_instructions = instructions.strip()
        if not normalized_instructions:
            raise InvalidManagedSkillError("Skill instructions must not be empty")
        content = (
            f"# {manifest.display_name}\n\n{manifest.summary}\n\n"
            f"{normalized_instructions}\n"
        )
        self._validate_skill_content(content, InvalidManagedSkillError)
        return self._publish(manifest, content)

    def install(self, package: bytes) -> ManagedSkillRecord:
        """处理 install。

        Args:
            package: package 参数。
        """
        if not package or len(package) > MAX_ARCHIVE_BYTES:
            raise InvalidSkillPackageError("Skill package size is invalid")
        resources: dict[PurePosixPath, bytes] = {}
        try:
            with zipfile.ZipFile(BytesIO(package)) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                if len(set(names)) != len(names):
                    raise InvalidSkillPackageError(
                        "Skill package must contain unique files"
                    )
                if not {"manifest.json", "SKILL.md"}.issubset(names):
                    raise InvalidSkillPackageError(
                        "Skill package must contain manifest.json and SKILL.md"
                    )
                for info in infos:
                    self._validate_zip_member(info)
                    path = PurePosixPath(info.filename)
                    if info.filename in {"manifest.json", "SKILL.md"}:
                        continue
                    self._validate_resource_member_path(path)
                    resource_bytes = self._read_zip_member(
                        archive,
                        info,
                        MAX_SKILL_RESOURCE_BYTES,
                    )
                    self._validate_resource_content(resource_bytes)
                    resources[path] = resource_bytes
                manifest_bytes = self._read_zip_member(
                    archive,
                    archive.getinfo("manifest.json"),
                    MAX_MANIFEST_BYTES,
                )
                skill_bytes = self._read_zip_member(
                    archive,
                    archive.getinfo("SKILL.md"),
                    MAX_SKILL_BYTES,
                )
        except ManagedSkillStoreError:
            raise
        except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise InvalidSkillPackageError("Skill package is not a valid ZIP") from exc

        try:
            raw_manifest = json.loads(manifest_bytes.decode("utf-8"))
            content = skill_bytes.decode("utf-8")
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidSkillPackageError(
                "Skill package text must be valid UTF-8 JSON and Markdown"
            ) from exc
        manifest = self._validated_manifest(raw_manifest, stored=False)
        self._validate_skill_content(content, InvalidSkillPackageError)
        return self._publish(manifest, content.strip() + "\n", resources=resources)

    def set_enabled(self, name: str, *, enabled: bool) -> ManagedSkillRecord:
        """处理 set enabled。

        Args:
            name: name 参数。
            enabled: enabled 参数。
        """
        record = self.get(name)
        manifest = _SkillManifest(
            schema_version=record.schema_version,
            name=record.name,
            display_name=record.display_name,
            summary=record.summary,
            version=record.version,
            enabled=enabled,
        )
        temporary = record.directory / ".manifest.json.tmp"
        try:
            temporary.write_text(self._serialize_manifest(manifest), encoding="utf-8")
            os.replace(temporary, record.directory / "manifest.json")
        finally:
            temporary.unlink(missing_ok=True)
        return replace(record, enabled=enabled)

    def uninstall(self, name: str) -> ManagedSkillRecord:
        """处理 uninstall。

        Args:
            name: name 参数。
        """
        record = self.get(name)
        resolved = record.directory.resolve(strict=True)
        if (
            record.directory.is_symlink()
            or not resolved.is_relative_to(self.managed_root)
            or resolved.parent != self.managed_root
        ):
            raise InvalidManagedSkillError("Managed Skill path is unsafe")
        shutil.rmtree(resolved)
        return record

    def load(self, name: str) -> SkillDefinition:
        """加载。

        Args:
            name: name 参数。
        """
        record = self.get(name)
        skill_file = record.directory / "SKILL.md"
        if skill_file.is_symlink() or not skill_file.is_file():
            raise InvalidManagedSkillError("Managed Skill instructions are unavailable")
        try:
            resolved = skill_file.resolve(strict=True)
            if not resolved.is_relative_to(self.managed_root):
                raise InvalidManagedSkillError("Managed Skill path is unsafe")
            if resolved.stat().st_size > MAX_SKILL_BYTES:
                raise InvalidManagedSkillError(
                    "Managed Skill instructions are oversized"
                )
            instructions = strip_skill_frontmatter(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise InvalidManagedSkillError(
                "Managed Skill instructions are unavailable"
            ) from exc
        if not instructions:
            raise InvalidManagedSkillError("Managed Skill instructions are empty")
        return SkillDefinition(
            name=record.name,
            instructions=instructions,
            source="managed",
            resources_root=record.directory,
        )

    def _publish(
        self,
        manifest: _SkillManifest,
        content: str,
        *,
        resources: Mapping[PurePosixPath, bytes] | None = None,
    ) -> ManagedSkillRecord:
        """执行 发布 的内部辅助逻辑。

        Args:
            manifest: manifest 参数。
            content: content 参数。
            resources: resources 参数。
        """
        self._assert_available(manifest.name)
        self.managed_root.mkdir(parents=True, exist_ok=True)
        if not self.managed_root.is_dir():
            raise InvalidManagedSkillError("Managed Skill root is unavailable")

        temporary_root = Path(
            tempfile.mkdtemp(prefix=".skill-stage-", dir=self.managed_root)
        )
        staged = temporary_root / manifest.name
        target = self.managed_root / manifest.name
        stored_manifest = replace(manifest, enabled=False)
        try:
            staged.mkdir()
            (staged / "manifest.json").write_text(
                self._serialize_manifest(stored_manifest),
                encoding="utf-8",
            )
            (staged / "SKILL.md").write_text(content, encoding="utf-8")
            for resource_path, resource_content in (resources or {}).items():
                destination = staged.joinpath(*resource_path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(resource_content)
            self._assert_available(manifest.name)
            staged.rename(target)
        except FileExistsError as exc:
            raise ManagedSkillConflictError(
                f"Skill already exists: {manifest.name}"
            ) from exc
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

        record = self._read_record(target)
        if record is None:
            raise InvalidManagedSkillError("Published Skill could not be verified")
        return record

    def _assert_available(self, name: str) -> None:
        """执行 处理 assert available 的内部辅助逻辑。

        Args:
            name: name 参数。
        """
        self._validate_name(name)
        if self._builtin_exists(name) or (self.managed_root / name).exists():
            raise ManagedSkillConflictError(f"Skill already exists: {name}")

    def _builtin_exists(self, name: str) -> bool:
        """执行 处理 builtin exists 的内部辅助逻辑。

        Args:
            name: name 参数。
        """
        if not _SAFE_SKILL_NAME.fullmatch(name):
            return False
        directory = self.builtin_root / name
        skill_file = directory / "SKILL.md"
        if directory.is_symlink() or skill_file.is_symlink():
            return False
        try:
            return (
                directory.is_dir()
                and skill_file.is_file()
                and skill_file.resolve(strict=True).is_relative_to(self.builtin_root)
            )
        except OSError:
            return False

    def _read_record(self, directory: Path) -> ManagedSkillRecord | None:
        """执行 处理 read record 的内部辅助逻辑。

        Args:
            directory: directory 参数。
        """
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or not _SAFE_SKILL_NAME.fullmatch(directory.name)
        ):
            return None
        manifest_file = directory / "manifest.json"
        skill_file = directory / "SKILL.md"
        if (
            manifest_file.is_symlink()
            or skill_file.is_symlink()
            or not manifest_file.is_file()
            or not skill_file.is_file()
        ):
            return None
        try:
            resolved_directory = directory.resolve(strict=True)
            resolved_manifest = manifest_file.resolve(strict=True)
            resolved_skill = skill_file.resolve(strict=True)
            if (
                not resolved_directory.is_relative_to(self.managed_root)
                or resolved_directory.parent != self.managed_root
                or not resolved_manifest.is_relative_to(resolved_directory)
                or not resolved_skill.is_relative_to(resolved_directory)
                or resolved_manifest.stat().st_size > MAX_MANIFEST_BYTES
                or not 0 < resolved_skill.stat().st_size <= MAX_SKILL_BYTES
            ):
                return None
            raw = json.loads(resolved_manifest.read_text(encoding="utf-8"))
            manifest = self._validated_manifest(raw, stored=True)
        except (ManagedSkillStoreError, OSError, UnicodeError, json.JSONDecodeError):
            return None
        if manifest.name != directory.name:
            return None
        return ManagedSkillRecord(
            **asdict(manifest),
            directory=resolved_directory,
        )

    @classmethod
    def _validated_manifest(
        cls,
        raw: object,
        *,
        stored: bool,
        error_type: type[ManagedSkillStoreError] | None = None,
    ) -> _SkillManifest:
        """执行 处理 validated manifest 的内部辅助逻辑。

        Args:
            raw: raw 参数。
            stored: stored 参数。
            error_type: error_type 参数。
        """
        error_type = error_type or (
            InvalidManagedSkillError if stored else InvalidSkillPackageError
        )
        if not isinstance(raw, dict):
            raise error_type("Skill manifest must be an object")
        expected = _STORED_KEYS if stored else _PACKAGE_KEYS
        if set(raw) != expected:
            raise error_type("Skill manifest fields are invalid")
        if raw.get("schema_version") != 1:
            raise error_type("Skill manifest schema is unsupported")
        name = raw.get("name")
        display_name = raw.get("display_name")
        summary = raw.get("summary")
        version = raw.get("version")
        enabled = raw.get("enabled", False)
        if not isinstance(name, str):
            raise error_type("Skill name is invalid")
        cls._validate_name(name, error_type=error_type)
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name.strip()) > MAX_DISPLAY_NAME_CHARS
        ):
            raise error_type("Skill display name is invalid")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary.strip()) > MAX_SUMMARY_CHARS
        ):
            raise error_type("Skill summary is invalid")
        if not isinstance(version, str) or not _SAFE_VERSION.fullmatch(version):
            raise error_type("Skill version is invalid")
        if not isinstance(enabled, bool):
            raise error_type("Skill enabled state is invalid")
        return _SkillManifest(
            schema_version=1,
            name=name,
            display_name=display_name.strip(),
            summary=summary.strip(),
            version=version,
            enabled=enabled,
        )

    @staticmethod
    def _validate_skill_content(
        content: str,
        error_type: type[ManagedSkillStoreError],
    ) -> None:
        """执行 校验 skill content 的内部辅助逻辑。

        Args:
            content: content 参数。
            error_type: error_type 参数。
        """
        if not content.strip() or len(content.encode("utf-8")) > MAX_SKILL_BYTES:
            raise error_type("Skill instructions are invalid")

    @staticmethod
    def _validate_name(
        name: str,
        *,
        error_type: type[ManagedSkillStoreError] = InvalidManagedSkillError,
    ) -> None:
        """执行 校验 name 的内部辅助逻辑。

        Args:
            name: name 参数。
            error_type: error_type 参数。
        """
        if not _SAFE_SKILL_NAME.fullmatch(name):
            raise error_type("Skill name must be safe kebab-case")

    @staticmethod
    def _validate_zip_member(info: zipfile.ZipInfo) -> None:
        """执行 校验 zip member 的内部辅助逻辑。

        Args:
            info: info 参数。
        """
        path = PurePosixPath(info.filename)
        mode = (info.external_attr >> 16) & 0o170000
        if (
            info.is_dir()
            or info.flag_bits & 0x1
            or path.is_absolute()
            or ".." in path.parts
            or mode == stat.S_IFLNK
            or info.file_size < 0
        ):
            raise InvalidSkillPackageError("Skill package member is unsafe")

    @staticmethod
    def _validate_resource_member_path(path: PurePosixPath) -> None:
        """执行 校验 resource member path 的内部辅助逻辑。

        Args:
            path: path 参数。
        """
        if (
            len(path.parts) < 2
            or path.parts[0] not in ALLOWED_SKILL_RESOURCE_DIRS
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise InvalidSkillPackageError(
                "Skill package contains unsupported resource paths"
            )

    @staticmethod
    def _validate_resource_content(content: bytes) -> None:
        """执行 校验 resource content 的内部辅助逻辑。

        Args:
            content: content 参数。
        """
        try:
            content.decode("utf-8")
        except UnicodeError as exc:
            raise InvalidSkillPackageError(
                "Skill package resources must be UTF-8 text"
            ) from exc

    @staticmethod
    def _read_zip_member(
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        limit: int,
    ) -> bytes:
        """执行 处理 read zip member 的内部辅助逻辑。

        Args:
            archive: archive 参数。
            info: info 参数。
            limit: limit 参数。
        """
        if info.file_size > limit:
            raise InvalidSkillPackageError("Skill package member is oversized")
        with archive.open(info) as member:
            content = member.read(limit + 1)
        if len(content) > limit:
            raise InvalidSkillPackageError("Skill package member is oversized")
        return content

    @staticmethod
    def _serialize_manifest(manifest: _SkillManifest) -> str:
        """执行 序列化 manifest 的内部辅助逻辑。

        Args:
            manifest: manifest 参数。
        """
        return (
            json.dumps(
                asdict(manifest),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
