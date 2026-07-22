from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import shutil
import time

from .types import DockerSandboxConfig, SandboxResult


_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class DockerSandboxRunner:
    """表示 处理 docker sandbox runner 的后端数据结构或服务对象。"""

    def __init__(self, *, config: DockerSandboxConfig, workspace_root: Path) -> None:
        """初始化对象实例。

        Args:
            config: config 参数。
            workspace_root: workspace_root 参数。
        """
        self.config = config
        self.workspace_root = workspace_root.expanduser().resolve()

    @property
    def available(self) -> bool:
        """处理 available。"""
        return bool(
            self.config.enabled
            and self.config.image
            and self.config.image in self.config.allowed_images
            and shutil.which("docker")
        )

    def build_argv(self, *, task_id: str, command: tuple[str, ...]) -> tuple[str, ...]:
        """构建 argv。

        Args:
            task_id: task_id 参数。
            command: command 参数。
        """
        if not self.config.enabled:
            raise RuntimeError("Docker sandbox is disabled")
        if not self.config.image or self.config.image not in self.config.allowed_images:
            raise RuntimeError("Docker sandbox image is not allowed")
        if not _SAFE_TASK_ID.fullmatch(task_id.strip()):
            raise ValueError("Invalid task id")
        if not command or len(command) > 64:
            raise ValueError("Sandbox command is empty or too long")
        normalized = tuple(str(item) for item in command)
        if any(not item or "\x00" in item or len(item) > 4_096 for item in normalized):
            raise ValueError("Sandbox command contains an invalid argument")

        workspace = self.workspace_root / task_id.strip()
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        resolved = workspace.resolve(strict=True)
        if resolved.parent != self.workspace_root:
            raise ValueError("Sandbox workspace escaped root")

        uid = os.getuid()
        gid = os.getgid()
        container_user = "65534:65534" if uid == 0 else f"{uid}:{gid}"
        return (
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(max(16, min(self.config.pids_limit, 256))),
            "--memory",
            f"{max(64, min(self.config.memory_mb, 1_024))}m",
            "--cpus",
            str(max(0.1, min(self.config.cpu_count, 2.0))),
            "--user",
            container_user,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,src={resolved},dst=/workspace,rw",
            "--workdir",
            "/workspace",
            self.config.image,
            *normalized,
        )

    async def execute(self, *, task_id: str, command: tuple[str, ...]) -> SandboxResult:
        """执行。

        Args:
            task_id: task_id 参数。
            command: command 参数。
        """
        if not self.available:
            raise RuntimeError("Docker sandbox is unavailable")
        argv = self.build_argv(task_id=task_id, command=command)
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=max(1.0, min(self.config.timeout_seconds, 120.0)),
            )
            timed_out = False
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            timed_out = True
        limit = max(1_000, min(self.config.max_output_chars, 100_000))
        return SandboxResult(
            stdout=stdout.decode("utf-8", errors="replace")[:limit],
            stderr=stderr.decode("utf-8", errors="replace")[:limit],
            exit_code=process.returncode,
            duration_ms=int((time.monotonic() - started) * 1_000),
            timed_out=timed_out,
        )
