from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PromptModuleName = Literal[
    "SYSTEM", "MEMORY_GUIDE", "TOOL_POLICY", "RESPONSE_STYLE", "AGENT_CONFIG"
]

MODULE_FILES: dict[PromptModuleName, str] = {
    "SYSTEM": "system.md",
    "MEMORY_GUIDE": "memory_guide.md",
    "TOOL_POLICY": "tool_policy.md",
    "RESPONSE_STYLE": "response_style.md",
    "AGENT_CONFIG": "agent_config.md",
}
FILE_TO_MODULE = {value: key for key, value in MODULE_FILES.items()}


@dataclass(frozen=True)
class PromptModule:
    """定义当前组件的职责和边界。"""

    name: PromptModuleName  # name 对应的数据字段。
    filename: str  # filename 对应的数据字段。
    content: str  # content 对应的数据字段。
    source: str  # source 对应的数据字段。
    fingerprint: str  # fingerprint 对应的数据字段。
    version: str | None = None  # version 对应的数据字段。
    metadata: dict[str, object] = field(default_factory=dict)  # metadata 对应的数据字段。


@dataclass(frozen=True)
class PromptBuildResult:
    """定义当前组件的职责和边界。"""

    system_prompt: str  # system_prompt 对应的数据字段。
    modules: tuple[PromptModule, ...]  # modules 对应的数据字段。
    fingerprint: str  # fingerprint 对应的数据字段。
    metadata: dict[str, object]  # metadata 对应的数据字段。
