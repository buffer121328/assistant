from __future__ import annotations

from dataclasses import dataclass

from agent.tool_management import ToolCatalogSnapshot, ToolSelectionResult


@dataclass(frozen=True)
class ToolCapability:
    name: str
    description: str
    enabled: bool
    approval_required: bool = False


@dataclass(frozen=True)
class CapabilitySnapshot:
    allowed_tools: tuple[str, ...]
    approval_required_tools: tuple[str, ...]
    summaries: tuple[str, ...]
    revision: int = 0
    tool_versions: tuple[tuple[str, str], ...] = ()
    selection_reasons: tuple[tuple[str, str], ...] = ()


class CapabilitiesBuilder:
    def __init__(self, capabilities: tuple[ToolCapability, ...]) -> None:
        self._capabilities = {capability.name: capability for capability in capabilities}
        self._revision = 0

    def refresh(self, capabilities: tuple[ToolCapability, ...]) -> None:
        self._capabilities = {
            capability.name: capability for capability in capabilities
        }
        self._revision += 1

    def build(self, *, requested_tools: tuple[str, ...]) -> CapabilitySnapshot:
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
