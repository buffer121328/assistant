from __future__ import annotations

from dataclasses import asdict
from typing import Any

from tools.core.registry import ToolInvocation, ToolRiskLevel, ToolSpec

from .constants import READONLY_SHELL_VERSION, WORKSPACE_TOOL_VERSION
from .schemas import (
    _find_files_schema,
    _list_schema,
    _read_file_schema,
    _readonly_shell_schema,
    _search_text_schema,
)
from .shell import ReadonlyShellRunner
from .store import WorkspaceContextStore
from .utils import _optional_int


def build_workspace_tool_specs(
    *,
    store: WorkspaceContextStore,
    readonly_shell: ReadonlyShellRunner | None = None,
) -> tuple[ToolSpec, ...]:
    """构建 workspace tool specs。

    Args:
        store: store 参数。
        readonly_shell: readonly_shell 参数。
    """

    async def list_workspace(invocation: ToolInvocation) -> Any:
        """列出 workspace。

        Args:
            invocation: invocation 参数。
        """
        args = invocation.arguments
        return store.list_dir(
            path=str(args.get("path") or "."),
            max_entries=_optional_int(args.get("max_entries")),
        )

    async def read_file(invocation: ToolInvocation) -> Any:
        """处理 read file。

        Args:
            invocation: invocation 参数。
        """
        args = invocation.arguments
        return store.read_file(
            path=str(args["path"]),
            max_bytes=_optional_int(args.get("max_bytes")),
        )

    async def search_text(invocation: ToolInvocation) -> Any:
        """搜索 text。

        Args:
            invocation: invocation 参数。
        """
        args = invocation.arguments
        return store.search_text(
            query=str(args["query"]),
            path=str(args.get("path") or "."),
            max_results=_optional_int(args.get("max_results")),
            max_file_bytes=_optional_int(args.get("max_file_bytes")),
        )

    async def find_files(invocation: ToolInvocation) -> Any:
        """处理 find files。

        Args:
            invocation: invocation 参数。
        """
        args = invocation.arguments
        return store.find_files(
            pattern=str(args["pattern"]),
            path=str(args.get("path") or "."),
            max_results=_optional_int(args.get("max_results")),
        )

    async def read_doc(invocation: ToolInvocation) -> Any:
        """处理 read doc。

        Args:
            invocation: invocation 参数。
        """
        args = invocation.arguments
        return store.read_doc(
            path=str(args["path"]),
            max_bytes=_optional_int(args.get("max_bytes")),
        )

    specs = [
        _spec(
            "workspace.list",
            "List files and directories in the configured workspace",
            list_workspace,
            _list_schema(),
        ),
        _spec(
            "workspace.read_file",
            "Read a bounded UTF-8 text file from the configured workspace",
            read_file,
            _read_file_schema(),
        ),
        _spec(
            "workspace.search_text",
            "Search bounded text files in the configured workspace",
            search_text,
            _search_text_schema(),
        ),
        _spec(
            "workspace.find_files",
            "Find files or directories in the configured workspace by glob",
            find_files,
            _find_files_schema(),
        ),
        _spec(
            "workspace.read_doc",
            "Read a bounded workspace document",
            read_doc,
            _read_file_schema(),
        ),
    ]
    if readonly_shell is not None and readonly_shell.available:

        async def shell_readonly(invocation: ToolInvocation) -> Any:
            """处理 shell readonly。

            Args:
                invocation: invocation 参数。
            """
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


def _spec(
    name: str,
    description: str,
    handler: Any,
    schema: dict[str, Any],
    *,
    risk_level: ToolRiskLevel = "L1",
) -> ToolSpec:
    """执行 处理 spec 的内部辅助逻辑。

    Args:
        name: name 参数。
        description: description 参数。
        handler: handler 参数。
        schema: schema 参数。
        risk_level: risk_level 参数。
    """
    return ToolSpec(
        name=name,
        description=description,
        risk_level=risk_level,
        handler=handler,
        input_schema=schema,
        version=(
            READONLY_SHELL_VERSION
            if name == "shell.readonly_exec"
            else WORKSPACE_TOOL_VERSION
        ),
        source_id="builtin",
    )
