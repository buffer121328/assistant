from __future__ import annotations

from dataclasses import asdict
import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

from domain.policies.redaction import sanitize_text

from .constants import DEFAULT_DENY_GLOBS, _BINARY_SUFFIXES, _TEXT_DOC_SUFFIXES
from .types import WorkspaceContextError, WorkspaceEntry, WorkspaceSearchMatch
from .utils import _compact


class WorkspaceContextStore:
    """表示 处理 workspace context store 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        root: Path,
        deny_globs: tuple[str, ...] = DEFAULT_DENY_GLOBS,
        max_file_bytes: int = 200_000,
        max_results: int = 50,
        sensitive_values: tuple[str | None, ...] = (),
    ) -> None:
        """初始化对象实例。

        Args:
            root: root 参数。
            deny_globs: deny_globs 参数。
            max_file_bytes: max_file_bytes 参数。
            max_results: max_results 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.root = root.expanduser().resolve(strict=True)
        self.deny_globs = tuple(item.strip() for item in deny_globs if item.strip())
        self.max_file_bytes = max(1_000, min(max_file_bytes, 2_000_000))
        self.max_results = max(1, min(max_results, 200))
        self.sensitive_values = sensitive_values

    @property
    def available(self) -> bool:
        """处理 available。"""
        return self.root.exists() and self.root.is_dir()

    def list_dir(
        self, *, path: str = ".", max_entries: int | None = None
    ) -> dict[str, Any]:
        """列出 dir。

        Args:
            path: path 参数。
            max_entries: max_entries 参数。
        """
        directory = self.resolve_path(path, require_file=False)
        if not directory.is_dir():
            raise WorkspaceContextError("Workspace path is not a directory")
        limit = self._bounded_count(max_entries)
        entries: list[WorkspaceEntry] = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            if self.is_denied(child):
                continue
            entries.append(
                WorkspaceEntry(
                    name=child.name,
                    path=self.relative_path(child),
                    type=(
                        "directory"
                        if child.is_dir()
                        else "file"
                        if child.is_file()
                        else "other"
                    ),
                    size=(child.stat().st_size if child.is_file() else None),
                )
            )
            if len(entries) >= limit:
                break
        return {
            "path": self.relative_path(directory),
            "entries": [asdict(entry) for entry in entries],
            "truncated": len(entries) >= limit,
        }

    def read_file(self, *, path: str, max_bytes: int | None = None) -> dict[str, Any]:
        """处理 read file。

        Args:
            path: path 参数。
            max_bytes: max_bytes 参数。
        """
        file_path = self._readable_file(path)
        limit = self._bounded_bytes(max_bytes)
        content = file_path.read_bytes()[:limit]
        text = self._decode_text(content)
        return {
            "path": self.relative_path(file_path),
            "content": self._safe_text(text),
            "bytes_read": len(content),
            "truncated": file_path.stat().st_size > limit,
        }

    def read_doc(self, *, path: str, max_bytes: int | None = None) -> dict[str, Any]:
        """处理 read doc。

        Args:
            path: path 参数。
            max_bytes: max_bytes 参数。
        """
        file_path = self._readable_file(path)
        if (
            file_path.suffix.lower() not in _TEXT_DOC_SUFFIXES
            and not file_path.name.lower().startswith("readme")
        ):
            raise WorkspaceContextError("Workspace document type is not supported")
        return self.read_file(path=path, max_bytes=max_bytes)

    def search_text(
        self,
        *,
        query: str,
        path: str = ".",
        max_results: int | None = None,
        max_file_bytes: int | None = None,
    ) -> dict[str, Any]:
        """搜索 text。

        Args:
            query: query 参数。
            path: path 参数。
            max_results: max_results 参数。
            max_file_bytes: max_file_bytes 参数。
        """
        needle = query.strip()
        if not needle:
            raise WorkspaceContextError("Search query is empty")
        base = self.resolve_path(path, require_file=False)
        limit = self._bounded_count(max_results)
        file_limit = self._bounded_bytes(max_file_bytes)
        matches: list[WorkspaceSearchMatch] = []
        for file_path in self._iter_files(base):
            if len(matches) >= limit:
                break
            if not self._is_supported_text_file(file_path, max_bytes=file_limit):
                continue
            try:
                text = self._decode_text(file_path.read_bytes()[:file_limit])
            except OSError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if needle.lower() not in line.lower():
                    continue
                matches.append(
                    WorkspaceSearchMatch(
                        path=self.relative_path(file_path),
                        line=line_number,
                        snippet=self._safe_text(_compact(line, 300)),
                    )
                )
                if len(matches) >= limit:
                    break
        return {
            "query": needle,
            "path": self.relative_path(base),
            "matches": [asdict(match) for match in matches],
            "truncated": len(matches) >= limit,
        }

    def find_files(
        self,
        *,
        pattern: str,
        path: str = ".",
        max_results: int | None = None,
    ) -> dict[str, Any]:
        """处理 find files。

        Args:
            pattern: pattern 参数。
            path: path 参数。
            max_results: max_results 参数。
        """
        safe_pattern = pattern.strip()
        if not safe_pattern or "\x00" in safe_pattern:
            raise WorkspaceContextError("File pattern is empty or invalid")
        if safe_pattern.startswith("/") or ".." in PurePosixPath(safe_pattern).parts:
            raise WorkspaceContextError("File pattern escapes workspace root")
        base = self.resolve_path(path, require_file=False)
        limit = self._bounded_count(max_results)
        results: list[WorkspaceEntry] = []
        for child in self._iter_children(base):
            rel = self.relative_path(child)
            if not fnmatch.fnmatch(rel, safe_pattern) and not fnmatch.fnmatch(
                child.name, safe_pattern
            ):
                continue
            if self.is_denied(child):
                continue
            results.append(
                WorkspaceEntry(
                    name=child.name,
                    path=rel,
                    type=(
                        "directory"
                        if child.is_dir()
                        else "file"
                        if child.is_file()
                        else "other"
                    ),
                    size=(child.stat().st_size if child.is_file() else None),
                )
            )
            if len(results) >= limit:
                break
        return {
            "pattern": safe_pattern,
            "path": self.relative_path(base),
            "matches": [asdict(entry) for entry in results],
            "truncated": len(results) >= limit,
        }

    def resolve_path(
        self, value: str = ".", *, require_file: bool | None = None
    ) -> Path:
        """解析 path。

        Args:
            value: value 参数。
            require_file: require_file 参数。
        """
        raw = (value or ".").strip()
        if not raw or "\x00" in raw:
            raise WorkspaceContextError("Workspace path is empty or invalid")
        candidate = Path(raw)
        if candidate.is_absolute():
            raise WorkspaceContextError("Absolute workspace paths are not allowed")
        if ".." in candidate.parts:
            raise WorkspaceContextError("Workspace path traversal is not allowed")
        resolved = (self.root / candidate).resolve(strict=True)
        if not resolved.is_relative_to(self.root):
            raise WorkspaceContextError("Workspace path escaped root")
        if self.is_denied(resolved):
            raise WorkspaceContextError("Workspace path is denied")
        if require_file is True and not resolved.is_file():
            raise WorkspaceContextError("Workspace path is not a file")
        if require_file is False and not resolved.exists():
            raise WorkspaceContextError("Workspace path does not exist")
        return resolved

    def relative_path(self, value: Path) -> str:
        """处理 relative path。

        Args:
            value: value 参数。
        """
        try:
            relative = value.resolve(strict=True).relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceContextError("Workspace path escaped root") from exc
        text = relative.as_posix()
        return text or "."

    def is_denied(self, value: Path) -> bool:
        """处理 is denied。

        Args:
            value: value 参数。
        """
        try:
            rel = value.resolve(strict=False).relative_to(self.root).as_posix()
        except ValueError:
            return True
        parts = PurePosixPath(rel).parts
        if any(part in {".git", "node_modules", "__pycache__"} for part in parts):
            return True
        if any(part.startswith(".env") for part in parts):
            return True
        path = PurePosixPath(rel)
        return any(
            path.match(pattern) or fnmatch.fnmatch(rel, pattern)
            for pattern in self.deny_globs
        )

    def _readable_file(self, path: str) -> Path:
        """执行 处理 readable file 的内部辅助逻辑。

        Args:
            path: path 参数。
        """
        file_path = self.resolve_path(path, require_file=True)
        if not self._is_supported_text_file(file_path, max_bytes=self.max_file_bytes):
            raise WorkspaceContextError(
                "Workspace file is not a supported bounded text file"
            )
        return file_path

    def _is_supported_text_file(self, file_path: Path, *, max_bytes: int) -> bool:
        """执行 处理 is supported text file 的内部辅助逻辑。

        Args:
            file_path: file_path 参数。
            max_bytes: max_bytes 参数。
        """
        if self.is_denied(file_path) or not file_path.is_file():
            return False
        if file_path.suffix.lower() in _BINARY_SUFFIXES:
            return False
        try:
            size = file_path.stat().st_size
            if size > max_bytes:
                return False
            sample = file_path.read_bytes()[: min(size, 4096)]
        except OSError:
            return False
        if b"\x00" in sample:
            return False
        try:
            sample.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True

    def _iter_files(self, base: Path):
        """执行 处理 iter files 的内部辅助逻辑。

        Args:
            base: base 参数。
        """
        if base.is_file():
            yield base
            return
        for child in self._iter_children(base):
            if child.is_file():
                yield child

    def _iter_children(self, base: Path):
        """执行 处理 iter children 的内部辅助逻辑。

        Args:
            base: base 参数。
        """
        if base.is_file():
            yield base
            return
        for child in sorted(base.rglob("*"), key=lambda item: item.as_posix().lower()):
            if self.is_denied(child):
                continue
            yield child

    def _decode_text(self, content: bytes) -> str:
        """执行 处理 decode text 的内部辅助逻辑。

        Args:
            content: content 参数。
        """
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceContextError("Workspace file is not UTF-8 text") from exc

    def _bounded_count(self, value: int | None) -> int:
        """执行 处理 bounded count 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        if value is None:
            return self.max_results
        return max(1, min(int(value), self.max_results, 200))

    def _bounded_bytes(self, value: int | None) -> int:
        """执行 处理 bounded bytes 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        if value is None:
            return self.max_file_bytes
        return max(1_000, min(int(value), self.max_file_bytes, 2_000_000))

    def _safe_text(self, value: object) -> str:
        """执行 处理 safe text 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        return sanitize_text(value, extra_sensitive_values=self.sensitive_values)
