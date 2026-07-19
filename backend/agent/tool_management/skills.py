from __future__ import annotations

from collections.abc import Sequence
import base64
from typing import Any, cast

from agent.skill_management.acquisition import SkillAcquisitionService, SkillCandidate

from .catalog import ToolDescriptor
from .registry import ToolInvocation, ToolRiskLevel, ToolSpec


SKILL_TOOL_VERSION = "v10-skills-v1"


def build_skill_tool_descriptors(*, enabled: bool = True) -> tuple[ToolDescriptor, ...]:
    return tuple(
        ToolDescriptor(
            name=name,
            description=description,
            input_schema=schema,
            source_id="builtin",
            source_kind="builtin",
            version=SKILL_TOOL_VERSION,
            enabled=enabled,
            risk_level=cast(ToolRiskLevel, risk),
            requires_approval=risk != "L1",
            tags=("skills", "capability", "v10"),
            parallel_safe=risk == "L1",
        )
        for name, description, risk, schema in _SKILL_TOOL_DEFS
    )


def build_skill_tool_specs(service: SkillAcquisitionService) -> tuple[ToolSpec, ...]:
    async def search(invocation: ToolInvocation) -> Any:
        decision = await service.recommend(
            capability_gap=str(invocation.arguments.get("capability_gap") or ""),
            query=str(invocation.arguments.get("query") or ""),
            tags=_string_list(invocation.arguments.get("tags")),
            allow_create=bool(invocation.arguments.get("allow_create", True)),
        )
        return decision.to_dict()

    async def recommend(invocation: ToolInvocation) -> Any:
        decision = await service.recommend(
            capability_gap=str(invocation.arguments.get("capability_gap") or ""),
            query=str(invocation.arguments.get("query") or ""),
            tags=_string_list(invocation.arguments.get("tags")),
            composable_tools=_string_list(invocation.arguments.get("composable_tools")),
            allow_create=bool(invocation.arguments.get("allow_create", True)),
        )
        return decision.to_dict()

    async def install_candidate(invocation: ToolInvocation) -> Any:
        candidate = _candidate_from_args(invocation.arguments)
        package = invocation.arguments.get("package_bytes")
        if not isinstance(package, bytes):
            package_b64 = invocation.arguments.get("package_b64")
            if not isinstance(package_b64, str):
                raise ValueError("package_b64 must be provided for marketplace installs")
            package = base64.b64decode(package_b64.encode("ascii"), validate=True)
        item = await service.install_candidate(
            user_id=invocation.user_id,
            candidate=candidate,
            package=package,
        )
        return {"name": item.name, "enabled": item.enabled, "source": item.source}

    async def propose_create(invocation: ToolInvocation) -> Any:
        change = await service.propose_create(
            session=service.lifecycle.session,
            task_id=invocation.task_id,
            user_id=invocation.user_id,
            name=str(invocation.arguments.get("name") or ""),
            instructions=str(invocation.arguments.get("instructions") or ""),
            evidence=str(invocation.arguments.get("evidence") or ""),
        )
        return {"change_id": change.id, "status": change.status, "target_name": change.target_name}

    async def enable(invocation: ToolInvocation) -> Any:
        item = await service.enable(user_id=invocation.user_id, name=str(invocation.arguments.get("name") or ""))
        return {"name": item.name, "enabled": item.enabled}

    async def disable(invocation: ToolInvocation) -> Any:
        item = await service.disable(user_id=invocation.user_id, name=str(invocation.arguments.get("name") or ""))
        return {"name": item.name, "enabled": item.enabled}

    async def refresh(invocation: ToolInvocation) -> Any:
        return service.refresh_capabilities()

    handlers = {
        "skills.search": search,
        "skills.recommend": recommend,
        "skills.install_candidate": install_candidate,
        "skills.propose_create": propose_create,
        "skills.enable": enable,
        "skills.disable": disable,
        "skills.refresh_capabilities": refresh,
    }
    return tuple(
        ToolSpec(
            name=name,
            description=description,
            risk_level=cast(ToolRiskLevel, risk),
            handler=handlers[name],
            input_schema=schema,
            version=SKILL_TOOL_VERSION,
            source_id="builtin",
        )
        for name, description, risk, schema in _SKILL_TOOL_DEFS
    )


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _candidate_from_args(arguments: dict[str, Any]) -> SkillCandidate:
    return SkillCandidate(
        skill_id=str(arguments.get("skill_id") or arguments.get("name") or ""),
        name=str(arguments.get("name") or ""),
        display_name=str(arguments.get("display_name") or arguments.get("name") or ""),
        summary=str(arguments.get("summary") or ""),
        version=str(arguments.get("version") or "1.0.0"),
        source=str(arguments.get("source") or "marketplace"),
        trust_level=str(arguments.get("trust_level") or "curated"),
        capability_match=float(arguments.get("capability_match") or 1.0),
    ).scored()


_STRING_ARRAY = {"type": "array", "items": {"type": "string"}, "default": []}
_SKILL_TOOL_DEFS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    (
        "skills.search",
        "Search local and marketplace Skills for a capability gap",
        "L1",
        {"type": "object", "properties": {"query": {"type": "string"}, "capability_gap": {"type": "string"}, "tags": _STRING_ARRAY, "allow_create": {"type": "boolean", "default": True}}, "additionalProperties": False},
    ),
    (
        "skills.recommend",
        "Recommend reuse, install, compose, create, or no safe Skill option",
        "L1",
        {"type": "object", "properties": {"query": {"type": "string"}, "capability_gap": {"type": "string"}, "tags": _STRING_ARRAY, "composable_tools": _STRING_ARRAY, "allow_create": {"type": "boolean", "default": True}}, "additionalProperties": False},
    ),
    (
        "skills.install_candidate",
        "Install an approved marketplace Skill package as disabled by default",
        "L3",
        {"type": "object", "properties": {"skill_id": {"type": "string"}, "name": {"type": "string"}, "display_name": {"type": "string"}, "summary": {"type": "string"}, "version": {"type": "string"}, "source": {"type": "string"}, "trust_level": {"type": "string"}, "capability_match": {"type": "number", "default": 1.0}, "dependency_risk": {"type": "string", "default": "low"}, "permission_risk": {"type": "string", "default": "low"}, "package_b64": {"type": "string"}}, "required": ["name", "package_b64"], "additionalProperties": False},
    ),
    (
        "skills.propose_create",
        "Create a governed Skill candidate proposal without enabling it",
        "L2",
        {"type": "object", "properties": {"name": {"type": "string"}, "instructions": {"type": "string"}, "evidence": {"type": "string"}}, "required": ["name", "instructions"], "additionalProperties": False},
    ),
    (
        "skills.enable",
        "Enable an installed managed Skill with governance",
        "L2",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
    ),
    (
        "skills.disable",
        "Disable an installed managed Skill",
        "L2",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
    ),
    (
        "skills.refresh_capabilities",
        "Refresh Skill-derived capabilities after an approved change",
        "L1",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
)
