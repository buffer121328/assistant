from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from capabilities import (
    CapabilityDisabledError,
    CapabilityKind,
    CapabilityLoaderMissingError,
    CapabilityMetadata,
    CapabilityNotFoundError,
    CapabilityRegistry,
    DuplicateCapabilityError,
    build_default_registry,
    discover_skill_metadata,
)
from tools import (
    ToolInvocation,
    ToolNotAllowedError,
    ToolRegistry,
    ToolSpec,
)


ROOT = Path(__file__).parents[2]


def metadata(
    capability_id: str,
    *,
    kind: CapabilityKind = CapabilityKind.SKILL,
    enabled: bool = True,
) -> CapabilityMetadata:
    return CapabilityMetadata(
        id=capability_id,
        kind=kind,
        display_name=capability_id,
        summary=f"Summary for {capability_id}",
        source="test",
        enabled=enabled,
        risk_level="L1",
        requires_approval=False,
    )


def test_default_catalog_maps_current_four_capability_kinds() -> None:
    registry = build_default_registry(ROOT / "backend" / "resources" / "skillpacks")

    items = registry.list()
    ids = [item.id for item in items]

    assert {item.kind.value for item in items} == {
        "code",
        "agent_profile",
        "skill",
        "tool",
    }
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert {
        "code.memory",
        "code.status",
        "profile.plan",
        "profile.learn",
        "profile.daily",
        "profile.office",
        "skill.structured-planning",
        "skill.research",
        "skill.daily-report",
        "skill.office-writing",
        "tool.search.web",
    }.issubset(ids)


def test_catalog_access_is_metadata_only_and_resolution_is_revision_cached() -> None:
    calls: list[str] = []
    first_instance = object()

    def loader() -> object:
        calls.append("load")
        return first_instance

    registry = CapabilityRegistry()
    registry.register(metadata("skill.lazy"), loader=loader)

    assert registry.list()[0].id == "skill.lazy"
    assert registry.get("skill.lazy").summary == "Summary for skill.lazy"
    assert calls == []

    assert registry.resolve("skill.lazy") is first_instance
    assert registry.resolve("skill.lazy") is first_instance
    assert calls == ["load"]

    registry.register(metadata("code.new", kind=CapabilityKind.CODE))
    assert registry.resolve("skill.lazy") is first_instance
    assert calls == ["load", "load"]


def test_registry_rejects_duplicate_unknown_disabled_and_loaderless_entries() -> None:
    registry = CapabilityRegistry()
    registry.register(metadata("skill.ready"), loader=object)
    registry.register(metadata("skill.disabled", enabled=False), loader=object)
    registry.register(metadata("skill.metadata-only"))

    with pytest.raises(DuplicateCapabilityError):
        registry.register(metadata("skill.ready"))
    with pytest.raises(CapabilityNotFoundError):
        registry.resolve("skill.unknown")
    with pytest.raises(CapabilityDisabledError):
        registry.resolve("skill.disabled")
    with pytest.raises(CapabilityLoaderMissingError):
        registry.resolve("skill.metadata-only")


def test_skill_discovery_uses_frontmatter_metadata_with_heading_fallback(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    frontmatter = skills_root / "frontmatter-skill"
    fallback = skills_root / "safe-skill"
    invalid = skills_root / "Bad_Name"
    empty = skills_root / "empty"
    nested = skills_root / "container" / "nested"
    outside = tmp_path / "outside"
    frontmatter.mkdir(parents=True)
    fallback.mkdir()
    invalid.mkdir()
    empty.mkdir()
    nested.mkdir(parents=True)
    outside.mkdir()
    (frontmatter / "SKILL.md").write_text(
        "---\n"
        "name: Portable Skill\n"
        "description: Startup-level metadata from YAML.\n"
        "---\n\n"
        "# Body Heading\n\n" + ("Detailed body instructions are loaded later. " * 600),
        encoding="utf-8",
    )
    (fallback / "SKILL.md").write_text(
        "# Safe Skill\n\nA safe metadata summary.\n\nFull instructions.",
        encoding="utf-8",
    )
    (invalid / "SKILL.md").write_text("# Invalid", encoding="utf-8")
    (empty / "SKILL.md").write_text("", encoding="utf-8")
    (nested / "SKILL.md").write_text("# Nested", encoding="utf-8")
    (outside / "SKILL.md").write_text("# Outside", encoding="utf-8")
    (skills_root / "escaped").symlink_to(outside, target_is_directory=True)

    discovered = discover_skill_metadata(skills_root)

    assert [(item.id, item.display_name, item.summary) for item in discovered] == [
        (
            "skill.frontmatter-skill",
            "Portable Skill",
            "Startup-level metadata from YAML.",
        ),
        ("skill.safe-skill", "Safe Skill", "A safe metadata summary."),
    ]
    assert all(str(tmp_path) not in repr(item) for item in discovered)


def test_capability_api_returns_filtered_safe_metadata() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/api/capabilities", params={"kind": "skill", "enabled": "true"}
        )
        invalid = client.get("/api/capabilities", params={"kind": "plugin"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["revision"] >= 1
    assert payload["items"]
    assert {item["kind"] for item in payload["items"]} == {"skill"}
    assert [item["id"] for item in payload["items"]] == sorted(
        item["id"] for item in payload["items"]
    )
    assert set(payload["items"][0]) == {
        "id",
        "kind",
        "display_name",
        "summary",
        "source",
        "enabled",
        "risk_level",
        "requires_approval",
    }
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert str(ROOT).lower() not in serialized
    assert "loader" not in serialized
    assert "placeholder-" not in serialized
    assert "api_key" not in serialized
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_catalog_visibility_does_not_authorize_tool_execution() -> None:
    calls: list[str] = []

    async def handler(invocation: ToolInvocation) -> dict[str, str]:
        calls.append(invocation.name)
        return {"status": "unexpected"}

    catalog = CapabilityRegistry()
    catalog.register(
        metadata("tool.demo", kind=CapabilityKind.TOOL), loader=lambda: handler
    )
    session = MagicMock()
    session.flush = AsyncMock()
    registry = ToolRegistry(session=session)
    registry.register(
        ToolSpec(
            name="demo",
            description="Demo",
            risk_level="L1",
            handler=handler,
        )
    )

    with pytest.raises(ToolNotAllowedError):
        await registry.execute(
            ToolInvocation(
                task_id="task-1",
                user_id="user-1",
                name="demo",
            ),
            allowed_tools=(),
            approval_required_tools=(),
        )

    assert catalog.get("tool.demo").enabled is True
    assert calls == []
