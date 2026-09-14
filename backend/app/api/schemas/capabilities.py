from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from agent.capabilities import CapabilityKind, CapabilityMetadata


class CapabilityResponse(BaseModel):
    """表示 处理 capability response 的后端数据结构或服务对象。"""

    id: str
    kind: CapabilityKind
    display_name: str
    summary: str
    source: str
    enabled: bool
    risk_level: Literal["L0", "L1", "L2", "L3", "L4"]
    requires_approval: bool


class CapabilityCatalogResponse(BaseModel):
    """表示 处理 capability catalog response 的后端数据结构或服务对象。"""

    revision: int
    items: list[CapabilityResponse]


def capability_response(metadata: CapabilityMetadata) -> CapabilityResponse:
    """处理 capability response。

    Args:
        metadata: metadata 参数。
    """
    return CapabilityResponse(
        id=metadata.id,
        kind=metadata.kind,
        display_name=metadata.display_name,
        summary=metadata.summary,
        source=metadata.source,
        enabled=metadata.enabled,
        risk_level=metadata.risk_level,
        requires_approval=metadata.requires_approval,
    )
