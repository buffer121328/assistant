from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SandboxProvider = Literal["none", "docker"]


@dataclass(frozen=True)
class SandboxResult:
    """表示 处理 sandbox result 的后端数据结构或服务对象。"""

    stdout: str  # stdout 对应的数据字段。
    stderr: str  # stderr 对应的数据字段。
    exit_code: int | None  # exit_code 对应的数据字段。
    duration_ms: int  # duration_ms 对应的数据字段。
    timed_out: bool  # timed_out 对应的数据字段。


@dataclass(frozen=True)
class DockerSandboxConfig:
    """表示 处理 docker sandbox config 的后端数据结构或服务对象。"""

    enabled: bool = False  # enabled 对应的数据字段。
    image: str = ""  # image 对应的数据字段。
    allowed_images: tuple[str, ...] = ()  # allowed_images 对应的数据字段。
    memory_mb: int = 256  # memory_mb 对应的数据字段。
    cpu_count: float = 0.5  # cpu_count 对应的数据字段。
    pids_limit: int = 64  # pids_limit 对应的数据字段。
    timeout_seconds: float = 30.0  # timeout_seconds 对应的数据字段。
    max_output_chars: int = 20_000  # max_output_chars 对应的数据字段。
