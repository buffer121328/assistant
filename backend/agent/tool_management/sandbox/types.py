from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SandboxProvider = Literal["none", "docker"]


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool


@dataclass(frozen=True)
class DockerSandboxConfig:
    enabled: bool = False
    image: str = ""
    allowed_images: tuple[str, ...] = ()
    memory_mb: int = 256
    cpu_count: float = 0.5
    pids_limit: int = 64
    timeout_seconds: float = 30.0
    max_output_chars: int = 20_000
