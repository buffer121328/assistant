from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from packages.capabilities import CapabilityKind, CapabilityRegistry

from .schemas import CapabilityCatalogResponse, capability_response

router = APIRouter()


@router.get("/api/capabilities", response_model=CapabilityCatalogResponse)
def list_capabilities(
    request: Request,
    kind: Annotated[CapabilityKind | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
) -> CapabilityCatalogResponse:
    registry: CapabilityRegistry = request.app.state.capability_registry
    return CapabilityCatalogResponse(
        revision=registry.revision,
        items=[
            capability_response(metadata)
            for metadata in registry.list(kind=kind, enabled=enabled)
        ],
    )
