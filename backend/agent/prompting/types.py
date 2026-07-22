from __future__ import annotations

from dataclasses import dataclass
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
    """One prompt module loaded from default or managed prompt roots."""

    name: PromptModuleName
    filename: str
    content: str
    source: str
    fingerprint: str
    version: str | None = None


@dataclass(frozen=True)
class PromptBuildResult:
    """Built prompt text plus module metadata."""

    system_prompt: str
    modules: tuple[PromptModule, ...]
    fingerprint: str
    metadata: dict[str, object]
