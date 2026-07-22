from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class WorkspaceContextError(ValueError):
    """表示 处理 workspace context error 的后端数据结构或服务对象。"""

    pass


@dataclass(frozen=True)
class WorkspaceEntry:
    """表示 处理 workspace entry 的后端数据结构或服务对象。"""

    name: str
    path: str
    type: Literal["file", "directory", "other"]
    size: int | None = None


@dataclass(frozen=True)
class WorkspaceSearchMatch:
    """表示 处理 workspace search match 的后端数据结构或服务对象。"""

    path: str
    line: int
    snippet: str


@dataclass(frozen=True)
class ReadonlyShellResult:
    """表示 处理 readonly shell result 的后端数据结构或服务对象。"""

    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool
