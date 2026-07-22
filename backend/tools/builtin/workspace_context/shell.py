from __future__ import annotations

import asyncio
from pathlib import Path
import time

from domain.policies.redaction import sanitize_text

from .constants import _ALLOWED_READONLY_COMMANDS, _DANGEROUS_ARGS, _SHELL_META_CHARS
from .store import WorkspaceContextStore
from .types import ReadonlyShellResult, WorkspaceContextError


class ReadonlyShellRunner:
    """表示 处理 readonly shell runner 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        store: WorkspaceContextStore,
        enabled: bool = False,
        timeout_seconds: float = 10.0,
        max_output_chars: int = 50_000,
    ) -> None:
        """初始化对象实例。

        Args:
            store: store 参数。
            enabled: enabled 参数。
            timeout_seconds: timeout_seconds 参数。
            max_output_chars: max_output_chars 参数。
        """
        self.store = store
        self.enabled = enabled
        self.timeout_seconds = max(1.0, min(timeout_seconds, 60.0))
        self.max_output_chars = max(1_000, min(max_output_chars, 100_000))

    @property
    def available(self) -> bool:
        """处理 available。"""
        return self.enabled and self.store.available

    def validate(self, command: tuple[str, ...]) -> tuple[str, ...]:
        """校验。

        Args:
            command: command 参数。
        """
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
        """执行。

        Args:
            command: command 参数。
        """
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
        """执行 校验 arg 的内部辅助逻辑。

        Args:
            arg: arg 参数。
        """
        if not arg or "\x00" in arg or len(arg) > 1_000:
            raise WorkspaceContextError("Readonly shell argument is invalid")
        if any(char in arg for char in _SHELL_META_CHARS):
            raise WorkspaceContextError("Readonly shell metacharacters are not allowed")
        if arg in _DANGEROUS_ARGS:
            raise WorkspaceContextError("Readonly shell argument is dangerous")

    def _validate_command_flags(self, program: str, args: tuple[str, ...]) -> None:
        """执行 校验 command flags 的内部辅助逻辑。

        Args:
            program: program 参数。
            args: args 参数。
        """
        for arg in args:
            if arg in _DANGEROUS_ARGS:
                raise WorkspaceContextError("Readonly shell argument is dangerous")
            if program in {"grep", "rg"} and (
                arg.startswith("--include-from") or arg.startswith("--exclude-from")
            ):
                raise WorkspaceContextError(
                    "Readonly shell file-list flags are not allowed"
                )
            if program in {"head", "tail"} and arg in {"-f", "--follow"}:
                raise WorkspaceContextError("Readonly shell follow mode is not allowed")

    def _validate_path_args(self, program: str, args: tuple[str, ...]) -> None:
        # Validate arguments that are definitely paths or existing workspace entries.

        """执行 校验 path args 的内部辅助逻辑。

        Args:
            program: program 参数。
            args: args 参数。
        """
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
                non_options_before = [
                    item for item in args[:index] if not item.startswith("-")
                ]
                if not non_options_before:
                    continue
                if arg in {".", "./"} or "/" in arg or (self.store.root / arg).exists():
                    self.store.resolve_path(arg, require_file=None)

    def _safe_output(self, value: bytes) -> str:
        """执行 处理 safe output 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        text = value.decode("utf-8", errors="replace")[: self.max_output_chars]
        return sanitize_text(text, extra_sensitive_values=self.store.sensitive_values)
