from __future__ import annotations

from typing import Any

from tools.core.catalog import ToolDescriptor
from tools.core.registry import ToolRiskLevel

from .constants import READONLY_SHELL_VERSION, WORKSPACE_TOOL_VERSION
from .schemas import (
    _find_files_schema,
    _list_schema,
    _read_file_schema,
    _readonly_shell_schema,
    _search_text_schema,
)


def build_workspace_tool_descriptors(
    *,
    enabled: bool,
    readonly_shell_enabled: bool = False,
) -> tuple[ToolDescriptor, ...]:
    """构建 workspace tool descriptors。

    Args:
        enabled: enabled 参数。
        readonly_shell_enabled: readonly_shell_enabled 参数。
    """
    descriptors = tuple(
        _descriptor(name, description, schema, enabled=enabled, risk_level="L1")
        for name, description, schema in (
            (
                "workspace.list",
                "List files and directories in the configured workspace",
                _list_schema(),
            ),
            (
                "workspace.read_file",
                "Read a bounded UTF-8 text file from the configured workspace",
                _read_file_schema(),
            ),
            (
                "workspace.search_text",
                "Search bounded text files in the configured workspace",
                _search_text_schema(),
            ),
            (
                "workspace.find_files",
                "Find files or directories in the configured workspace by glob",
                _find_files_schema(),
            ),
            (
                "workspace.read_doc",
                "Read a bounded README, Markdown, reStructuredText, AsciiDoc, or text document",
                _read_file_schema(),
            ),
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


def _descriptor(
    name: str,
    description: str,
    schema: dict[str, Any],
    *,
    enabled: bool,
    risk_level: ToolRiskLevel,
) -> ToolDescriptor:
    """执行 处理 descriptor 的内部辅助逻辑。

    Args:
        name: name 参数。
        description: description 参数。
        schema: schema 参数。
        enabled: enabled 参数。
        risk_level: risk_level 参数。
    """
    return ToolDescriptor(
        name=name,
        description=description,
        input_schema=schema,
        source_id="builtin",
        source_kind="builtin",
        version=(
            READONLY_SHELL_VERSION
            if name == "shell.readonly_exec"
            else WORKSPACE_TOOL_VERSION
        ),
        enabled=enabled,
        risk_level=risk_level,
        requires_approval=False,
        tags=(
            "learn",
            "daily",
            "office",
            "plan",
            "v2.researcher",
            "v2.daily_reporter",
            "v2.office_writer",
            "v2.planner",
        ),
        parallel_safe=False,
    )
