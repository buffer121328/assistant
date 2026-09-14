from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class WorkspaceContextError(ValueError):
    """表示 处理 workspace context error 的后端数据结构或服务对象。"""

    pass


@dataclass(frozen=True)
class WorkspaceEntry:
    """表示 处理 workspace entry 的后端数据结构或服务对象。"""

    name: str  # name 对应的数据字段。
    path: str  # path 对应的数据字段。
    type: Literal["file", "directory", "other"]  # type 对应的数据字段。
    size: int | None = None  # size 对应的数据字段。


@dataclass(frozen=True)
class WorkspaceSearchMatch:
    """表示 处理 workspace search match 的后端数据结构或服务对象。"""

    path: str  # path 对应的数据字段。
    line: int  # line 对应的数据字段。
    snippet: str  # snippet 对应的数据字段。


@dataclass(frozen=True)
class ReadonlyShellResult:
    """表示 处理 readonly shell result 的后端数据结构或服务对象。"""

    stdout: str  # stdout 对应的数据字段。
    stderr: str  # stderr 对应的数据字段。
    exit_code: int | None  # exit_code 对应的数据字段。
    duration_ms: int  # duration_ms 对应的数据字段。
    timed_out: bool  # timed_out 对应的数据字段。
