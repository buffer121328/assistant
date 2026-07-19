from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import fnmatch
from pathlib import Path, PurePosixPath
import time
from typing import Any, Literal

from model_gateway import sanitize_text

from .catalog import ToolDescriptor
from .registry import ToolInvocation, ToolRiskLevel, ToolSpec


WORKSPACE_TOOL_VERSION = "workspace-context-v1"
READONLY_SHELL_VERSION = "readonly-shell-v1"

DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    ".git/**",
    "**/.git/**",
    "node_modules/**",
    "**/node_modules/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "*.pem",
    "**/*.pem",
    "*.key",
    "**/*.key",
    "*.p12",
    "**/*.p12",
    "*.sqlite",
    "**/*.sqlite",
    "*.db",
    "**/*.db",
)

_TEXT_DOC_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".rst", ".adoc"})
_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".avif",
        ".bin",
        ".bmp",
        ".class",
        ".dmg",
        ".doc",
        ".docx",
        ".DS_Store",
        ".eot",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".lockb",
        ".mp3",
        ".mp4",
        ".otf",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".pyc",
        ".so",
        ".tar",
        ".ttf",
        ".webp",
        ".woff",
        ".woff2",
        ".xls",
        ".xlsx",
        ".zip",
    }
)
_ALLOWED_READONLY_COMMANDS = frozenset(
    {"ls", "find", "grep", "rg", "cat", "head", "tail", "wc"}
)
_SHELL_META_CHARS = frozenset("|&;><`$(){}[]\n\r")
_DANGEROUS_ARGS = frozenset(
    {
        "-delete",
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-fprint",
        "-fprintf",
        "-fls",
        "-i",
        "--in-place",
        "--replace",
        "--files-from",
        "--pre",
        "--pre-glob",
    }
)


class WorkspaceContextError(ValueError):
    pass


@dataclass(frozen=True)
class WorkspaceEntry:
    name: str
    path: str
    type: Literal["file", "directory", "other"]
    size: int | None = None


@dataclass(frozen=True)
class WorkspaceSearchMatch:
    path: str
    line: int
    snippet: str


@dataclass(frozen=True)
class ReadonlyShellResult:
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool


class WorkspaceContextStore:
    def __init__(
        self,
        *,
        root: Path,
        deny_globs: tuple[str, ...] = DEFAULT_DENY_GLOBS,
        max_file_bytes: int = 200_000,
        max_results: int = 50,
        sensitive_values: tuple[str | None, ...] = (),
    ) -> None:
        self.root = root.expanduser().resolve(strict=True)
        self.deny_globs = tuple(item.strip() for item in deny_globs if item.strip())
        self.max_file_bytes = max(1_000, min(max_file_bytes, 2_000_000))
        self.max_results = max(1, min(max_results, 200))
        self.sensitive_values = sensitive_values

    @property
    def available(self) -> bool:
        return self.root.exists() and self.root.is_dir()

    def list_dir(self, *, path: str = ".", max_entries: int | None = None) -> dict[str, Any]:
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
                    type=("directory" if child.is_dir() else "file" if child.is_file() else "other"),
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
        file_path = self._readable_file(path)
        if file_path.suffix.lower() not in _TEXT_DOC_SUFFIXES and not file_path.name.lower().startswith("readme"):
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
            if not fnmatch.fnmatch(rel, safe_pattern) and not fnmatch.fnmatch(child.name, safe_pattern):
                continue
            if self.is_denied(child):
                continue
            results.append(
                WorkspaceEntry(
                    name=child.name,
                    path=rel,
                    type=("directory" if child.is_dir() else "file" if child.is_file() else "other"),
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

    def resolve_path(self, value: str = ".", *, require_file: bool | None = None) -> Path:
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
        try:
            relative = value.resolve(strict=True).relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceContextError("Workspace path escaped root") from exc
        text = relative.as_posix()
        return text or "."

    def is_denied(self, value: Path) -> bool:
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
        return any(path.match(pattern) or fnmatch.fnmatch(rel, pattern) for pattern in self.deny_globs)

    def _readable_file(self, path: str) -> Path:
        file_path = self.resolve_path(path, require_file=True)
        if not self._is_supported_text_file(file_path, max_bytes=self.max_file_bytes):
            raise WorkspaceContextError("Workspace file is not a supported bounded text file")
        return file_path

    def _is_supported_text_file(self, file_path: Path, *, max_bytes: int) -> bool:
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
        if base.is_file():
            yield base
            return
        for child in self._iter_children(base):
            if child.is_file():
                yield child

    def _iter_children(self, base: Path):
        if base.is_file():
            yield base
            return
        for child in sorted(base.rglob("*"), key=lambda item: item.as_posix().lower()):
            if self.is_denied(child):
                continue
            yield child

    def _decode_text(self, content: bytes) -> str:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceContextError("Workspace file is not UTF-8 text") from exc

    def _bounded_count(self, value: int | None) -> int:
        if value is None:
            return self.max_results
        return max(1, min(int(value), self.max_results, 200))

    def _bounded_bytes(self, value: int | None) -> int:
        if value is None:
            return self.max_file_bytes
        return max(1_000, min(int(value), self.max_file_bytes, 2_000_000))

    def _safe_text(self, value: object) -> str:
        return sanitize_text(value, extra_sensitive_values=self.sensitive_values)


class ReadonlyShellRunner:
    def __init__(
        self,
        *,
        store: WorkspaceContextStore,
        enabled: bool = False,
        timeout_seconds: float = 10.0,
        max_output_chars: int = 50_000,
    ) -> None:
        self.store = store
        self.enabled = enabled
        self.timeout_seconds = max(1.0, min(timeout_seconds, 60.0))
        self.max_output_chars = max(1_000, min(max_output_chars, 100_000))

    @property
    def available(self) -> bool:
        return self.enabled and self.store.available

    def validate(self, command: tuple[str, ...]) -> tuple[str, ...]:
        if not self.enabled:
            raise WorkspaceContextError("Readonly shell is disabled")
        if not command or len(command) > 32:
            raise WorkspaceContextError("Readonly shell command is empty or too long")
        normalized = tuple(str(item) for item in command)
        program = normalized[0]
        if Path(program).name != program or program not in _ALLOWED_READONLY_COMMANDS:
            raise WorkspaceContextError("Readonly shell command is not allowed")
        for arg in normalized:
            self._validate_arg(arg)
        self._validate_command_flags(program, normalized[1:])
        self._validate_path_args(program, normalized[1:])
        return normalized

    async def execute(self, command: tuple[str, ...]) -> ReadonlyShellResult:
        argv = self.validate(command)
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self.store.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
            timed_out = False
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            timed_out = True
        return ReadonlyShellResult(
            stdout=self._safe_output(stdout),
            stderr=self._safe_output(stderr),
            exit_code=process.returncode,
            duration_ms=int((time.monotonic() - started) * 1_000),
            timed_out=timed_out,
        )

    def _validate_arg(self, arg: str) -> None:
        if not arg or "\x00" in arg or len(arg) > 1_000:
            raise WorkspaceContextError("Readonly shell argument is invalid")
        if any(char in arg for char in _SHELL_META_CHARS):
            raise WorkspaceContextError("Readonly shell metacharacters are not allowed")
        if arg in _DANGEROUS_ARGS:
            raise WorkspaceContextError("Readonly shell argument is dangerous")

    def _validate_command_flags(self, program: str, args: tuple[str, ...]) -> None:
        for arg in args:
            if arg in _DANGEROUS_ARGS:
                raise WorkspaceContextError("Readonly shell argument is dangerous")
            if program in {"grep", "rg"} and (arg.startswith("--include-from") or arg.startswith("--exclude-from")):
                raise WorkspaceContextError("Readonly shell file-list flags are not allowed")
            if program in {"head", "tail"} and arg in {"-f", "--follow"}:
                raise WorkspaceContextError("Readonly shell follow mode is not allowed")

    def _validate_path_args(self, program: str, args: tuple[str, ...]) -> None:
        # Validate arguments that are definitely paths or existing workspace entries.
        if program == "find":
            for arg in args:
                if arg.startswith("-"):
                    break
                self.store.resolve_path(arg, require_file=None)
            return

        for index, arg in enumerate(args):
            if arg.startswith("-"):
                continue
            if program in {"cat", "head", "tail", "wc", "ls"}:
                self.store.resolve_path(arg, require_file=None)
                continue
            if program in {"grep", "rg"}:
                # First non-option grep/rg arg is usually a pattern. Later path-like or existing args are targets.
                non_options_before = [item for item in args[:index] if not item.startswith("-")]
                if not non_options_before:
                    continue
                if arg in {".", "./"} or "/" in arg or (self.store.root / arg).exists():
                    self.store.resolve_path(arg, require_file=None)

    def _safe_output(self, value: bytes) -> str:
        text = value.decode("utf-8", errors="replace")[: self.max_output_chars]
        return sanitize_text(text, extra_sensitive_values=self.store.sensitive_values)


def build_workspace_tool_descriptors(
    *,
    enabled: bool,
    readonly_shell_enabled: bool = False,
) -> tuple[ToolDescriptor, ...]:
    descriptors = tuple(
        _descriptor(name, description, schema, enabled=enabled, risk_level="L1")
        for name, description, schema in (
            ("workspace.list", "List files and directories in the configured workspace", _list_schema()),
            ("workspace.read_file", "Read a bounded UTF-8 text file from the configured workspace", _read_file_schema()),
            ("workspace.search_text", "Search bounded text files in the configured workspace", _search_text_schema()),
            ("workspace.find_files", "Find files or directories in the configured workspace by glob", _find_files_schema()),
            ("workspace.read_doc", "Read a bounded README, Markdown, reStructuredText, AsciiDoc, or text document", _read_file_schema()),
        )
    )
    return (
        *descriptors,
        _descriptor(
            "shell.readonly_exec",
            "Execute an explicitly enabled readonly argv command in the configured workspace",
            _readonly_shell_schema(),
            enabled=readonly_shell_enabled,
            risk_level="L2",
        ),
    )


def build_workspace_tool_specs(
    *,
    store: WorkspaceContextStore,
    readonly_shell: ReadonlyShellRunner | None = None,
) -> tuple[ToolSpec, ...]:
    async def list_workspace(invocation: ToolInvocation) -> Any:
        args = invocation.arguments
        return store.list_dir(
            path=str(args.get("path") or "."),
            max_entries=_optional_int(args.get("max_entries")),
        )

    async def read_file(invocation: ToolInvocation) -> Any:
        args = invocation.arguments
        return store.read_file(
            path=str(args["path"]),
            max_bytes=_optional_int(args.get("max_bytes")),
        )

    async def search_text(invocation: ToolInvocation) -> Any:
        args = invocation.arguments
        return store.search_text(
            query=str(args["query"]),
            path=str(args.get("path") or "."),
            max_results=_optional_int(args.get("max_results")),
            max_file_bytes=_optional_int(args.get("max_file_bytes")),
        )

    async def find_files(invocation: ToolInvocation) -> Any:
        args = invocation.arguments
        return store.find_files(
            pattern=str(args["pattern"]),
            path=str(args.get("path") or "."),
            max_results=_optional_int(args.get("max_results")),
        )

    async def read_doc(invocation: ToolInvocation) -> Any:
        args = invocation.arguments
        return store.read_doc(
            path=str(args["path"]),
            max_bytes=_optional_int(args.get("max_bytes")),
        )

    specs = [
        _spec("workspace.list", "List files and directories in the configured workspace", list_workspace, _list_schema()),
        _spec("workspace.read_file", "Read a bounded UTF-8 text file from the configured workspace", read_file, _read_file_schema()),
        _spec("workspace.search_text", "Search bounded text files in the configured workspace", search_text, _search_text_schema()),
        _spec("workspace.find_files", "Find files or directories in the configured workspace by glob", find_files, _find_files_schema()),
        _spec("workspace.read_doc", "Read a bounded workspace document", read_doc, _read_file_schema()),
    ]
    if readonly_shell is not None and readonly_shell.available:
        async def shell_readonly(invocation: ToolInvocation) -> Any:
            command = tuple(str(item) for item in invocation.arguments["command"])
            return asdict(await readonly_shell.execute(command))

        specs.append(
            _spec(
                "shell.readonly_exec",
                "Execute an explicitly enabled readonly argv command in the configured workspace",
                shell_readonly,
                _readonly_shell_schema(),
                risk_level="L2",
            )
        )
    return tuple(specs)


def _descriptor(
    name: str,
    description: str,
    schema: dict[str, Any],
    *,
    enabled: bool,
    risk_level: ToolRiskLevel,
) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=description,
        input_schema=schema,
        source_id="builtin",
        source_kind="builtin",
        version=(READONLY_SHELL_VERSION if name == "shell.readonly_exec" else WORKSPACE_TOOL_VERSION),
        enabled=enabled,
        risk_level=risk_level,
        requires_approval=False,
        tags=("learn", "daily", "office", "plan", "v2.researcher", "v2.daily_reporter", "v2.office_writer", "v2.planner"),
        parallel_safe=False,
    )


def _spec(
    name: str,
    description: str,
    handler: Any,
    schema: dict[str, Any],
    *,
    risk_level: ToolRiskLevel = "L1",
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        risk_level=risk_level,
        handler=handler,
        input_schema=schema,
        version=(READONLY_SHELL_VERSION if name == "shell.readonly_exec" else WORKSPACE_TOOL_VERSION),
        source_id="builtin",
    )


def _object(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _text(max_length: int = 2_048, *, allow_empty: bool = False) -> dict[str, Any]:
    return {"type": "string", "minLength": 0 if allow_empty else 1, "maxLength": max_length}


def _positive_int(maximum: int) -> dict[str, Any]:
    return {"type": "integer", "minimum": 1, "maximum": maximum}


def _list_schema() -> dict[str, Any]:
    return _object(
        {"path": _text(1_024, allow_empty=True), "max_entries": _positive_int(200)},
        (),
    )


def _read_file_schema() -> dict[str, Any]:
    return _object(
        {"path": _text(1_024), "max_bytes": _positive_int(2_000_000)},
        ("path",),
    )


def _search_text_schema() -> dict[str, Any]:
    return _object(
        {
            "query": _text(500),
            "path": _text(1_024, allow_empty=True),
            "max_results": _positive_int(200),
            "max_file_bytes": _positive_int(2_000_000),
        },
        ("query",),
    )


def _find_files_schema() -> dict[str, Any]:
    return _object(
        {"pattern": _text(500), "path": _text(1_024, allow_empty=True), "max_results": _positive_int(200)},
        ("pattern",),
    )


def _readonly_shell_schema() -> dict[str, Any]:
    return _object(
        {"command": {"type": "array", "items": _text(1_000), "minItems": 1, "maxItems": 32}},
        ("command",),
    )


def parse_deny_globs(value: str | tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_DENY_GLOBS
    if isinstance(value, str):
        items = tuple(item.strip() for item in value.split(",") if item.strip())
    else:
        items = tuple(str(item).strip() for item in value if str(item).strip())
    return items or DEFAULT_DENY_GLOBS


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _compact(value: str, limit: int) -> str:
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."
