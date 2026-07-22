from __future__ import annotations

from dataclasses import dataclass

from agent.tool_management import ToolCatalogSnapshot, ToolSelectionResult


@dataclass(frozen=True)
class ToolCapability:
    """表示 处理 tool capability 的后端数据结构或服务对象。"""

    name: str
    description: str
    enabled: bool
    approval_required: bool = False


@dataclass(frozen=True)
class CapabilitySnapshot:
    """表示 处理 capability snapshot 的后端数据结构或服务对象。"""

    allowed_tools: tuple[str, ...]
    approval_required_tools: tuple[str, ...]
    summaries: tuple[str, ...]
    revision: int = 0
    tool_versions: tuple[tuple[str, str], ...] = ()
    selection_reasons: tuple[tuple[str, str], ...] = ()


class CapabilitiesBuilder:
    """表示 处理 capabilities builder 的后端数据结构或服务对象。"""

    def __init__(self, capabilities: tuple[ToolCapability, ...]) -> None:
        """初始化对象实例。

        Args:
            capabilities: capabilities 参数。
        """
        self._capabilities = {
            capability.name: capability for capability in capabilities
        }
        self._revision = 0

    def refresh(self, capabilities: tuple[ToolCapability, ...]) -> None:
        """处理 refresh。

        Args:
            capabilities: capabilities 参数。
        """
        self._capabilities = {
            capability.name: capability for capability in capabilities
        }
        self._revision += 1

    def build(self, *, requested_tools: tuple[str, ...]) -> CapabilitySnapshot:
        """构建。

        Args:
            requested_tools: requested_tools 参数。
        """
        allowed: list[str] = []
        approval_required: list[str] = []
        summaries: list[str] = []

        for name in dict.fromkeys(requested_tools):
            capability = self._capabilities.get(name)
            if capability is None or not capability.enabled:
                continue
            summaries.append(f"{capability.name}: {capability.description}")
            if capability.approval_required:
                approval_required.append(capability.name)
            else:
                allowed.append(capability.name)

        return CapabilitySnapshot(
            revision=self._revision,
            allowed_tools=tuple(allowed),
            approval_required_tools=tuple(approval_required),
            summaries=tuple(summaries),
        )


def snapshot_from_tool_selection(
    catalog: ToolCatalogSnapshot,
    selection: ToolSelectionResult,
) -> CapabilitySnapshot:
    """处理 snapshot from tool selection。

    Args:
        catalog: catalog 参数。
        selection: selection 参数。
    """
    selected_names = set(selection.names)
    summaries = tuple(
        f"{descriptor.name}: {descriptor.description}"
        for descriptor in catalog.descriptors
        if descriptor.name in selected_names
    )
    return CapabilitySnapshot(
        revision=selection.snapshot_revision,
        allowed_tools=selection.allowed_tools,
        approval_required_tools=selection.approval_required_tools,
        summaries=summaries,
        tool_versions=selection.versions,
        selection_reasons=selection.reasons,
    )
