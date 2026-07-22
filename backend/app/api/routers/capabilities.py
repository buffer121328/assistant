from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from capabilities import CapabilityKind, CapabilityRegistry

from app.api.schemas import CapabilityCatalogResponse, capability_response

router = APIRouter()


@router.get("/api/capabilities", response_model=CapabilityCatalogResponse)
def list_capabilities(
    request: Request,
    kind: Annotated[CapabilityKind | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
) -> CapabilityCatalogResponse:
    """列出 capabilities。

    Args:
        request: request 参数。
        kind: kind 参数。
        enabled: enabled 参数。
    """
    registry: CapabilityRegistry = request.app.state.capability_registry
    return CapabilityCatalogResponse(
        revision=registry.revision,
        items=[
            capability_response(metadata)
            for metadata in registry.list(kind=kind, enabled=enabled)
        ],
    )
