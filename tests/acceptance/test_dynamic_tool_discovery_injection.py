from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import (
    DefaultPlanningLayer,
    DefaultProfileSelector,
    TaskContext,
)
from capabilities import CapabilityKind, build_default_registry
from agent.tool_management import (
    MCPToolDescription,
    MCPToolSource,
    StaticToolSource,
    ToolCandidateSelector,
    ToolCatalog,
    ToolDescriptor,
    ToolInvocation,
    ToolNotAllowedError,
    ToolRegistry,
    ToolSnapshotStaleError,
    ToolSourceStatus,
    ToolSpec,
    build_planned_tool_schemas,
)


ROOT = Path(__file__).parents[2]


def descriptor(
    name: str,
    *,
    source_id: str = "builtin",
    source_kind: Literal["builtin", "mcp"] = "builtin",
    version: str = "1",
    enabled: bool = True,
    tags: tuple[str, ...] = (),
    always_available: bool = False,
) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=f"Safe description for {name}",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        source_id=source_id,
        source_kind=source_kind,
        version=version,
        enabled=enabled,
        risk_level="L1",
        requires_approval=False,
        tags=tags,
        always_available=always_available,
    )


class MutableToolSource:
    def __init__(
        self,
        source_id: str,
        items: tuple[ToolDescriptor, ...],
        *,
        source_kind: Literal["builtin", "mcp"] = "builtin",
    ) -> None:
        self.source_id = source_id
        self.source_kind: Literal["builtin", "mcp"] = source_kind
        self.items = items
        self.error: Exception | None = None
        self.calls = 0

    async def discover(self) -> tuple[ToolDescriptor, ...]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.items


@pytest.mark.asyncio
async def test_snapshot_refresh_is_versioned_atomic_and_reports_diff() -> None:
    source = MutableToolSource(
        "builtin",
        (
            descriptor("demo.keep"),
            descriptor("demo.remove"),
            descriptor("demo.disable"),
        ),
    )
    catalog = ToolCatalog((source,))

    first = await catalog.refresh()
    source.items = (
        descriptor("demo.keep", version="2"),
        descriptor("demo.disable", enabled=False),
        descriptor("demo.add"),
    )
    second = await catalog.refresh()

    assert first.revision == 1
    assert second.revision == 2
    assert second.diff.added == ("demo.add",)
    assert second.diff.updated == ("demo.keep",)
    assert second.diff.disabled == ("demo.disable",)
    assert second.diff.removed == ("demo.remove",)
    assert [item.name for item in second.descriptors] == [
        "demo.add",
        "demo.disable",
        "demo.keep",
    ]


@pytest.mark.asyncio
async def test_invalid_duplicate_descriptor_is_rejected_without_replacement() -> None:
    valid = descriptor("demo.same")
    duplicate = descriptor("demo.same", version="2")
    source = MutableToolSource("builtin", (valid, duplicate))

    snapshot = await ToolCatalog((source,)).refresh()

    assert snapshot.get("demo.same") == valid
    assert snapshot.source_status("builtin").available is True
    assert "duplicate" in (snapshot.source_status("builtin").error or "").lower()


@pytest.mark.asyncio
async def test_refresh_does_not_scan_or_import_arbitrary_python(tmp_path: Path) -> None:
    sentinel = tmp_path / "executed.txt"
    (tmp_path / "untrusted_tool.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )

    await ToolCatalog((StaticToolSource("builtin", (descriptor("demo.safe"),)),)).refresh()

    assert not sentinel.exists()


class FakeMCPDiscoveryClient:
    def __init__(self) -> None:
        self.calls = 0
        self.error: Exception | None = None

    async def list_tools(self) -> tuple[MCPToolDescription, ...]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return (
            MCPToolDescription(
                name="mcp.notes.read",
                description="Read notes",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        )


@pytest.mark.asyncio
async def test_mcp_discovery_requires_explicit_client_and_is_disabled_by_default() -> None:
    absent = MCPToolSource(source_id="notes", client=None)
    absent_snapshot = await ToolCatalog((absent,)).refresh()

    client = FakeMCPDiscoveryClient()
    configured = MCPToolSource(source_id="notes", client=client)
    configured_snapshot = await ToolCatalog((configured,)).refresh()

    assert absent_snapshot.descriptors == ()
    assert client.calls == 1
    discovered = configured_snapshot.get("mcp.notes.read")
    assert discovered is not None
    assert discovered.source_kind == "mcp"
    assert discovered.enabled is False


@pytest.mark.asyncio
async def test_source_failure_is_isolated_sanitized_and_can_recover() -> None:
    builtin = MutableToolSource("builtin", (descriptor("core.clock"),))
    remote = MutableToolSource(
        "remote",
        (
            descriptor(
                "mcp.remote.read",
                source_id="remote",
                source_kind="mcp",
            ),
        ),
        source_kind="mcp",
    )
    secret = "private-token-value"
    catalog = ToolCatalog((builtin, remote), sensitive_values=(secret,))
    await catalog.refresh()

    remote.error = RuntimeError(
        f"Traceback Authorization: Bearer {secret} https://private.invalid"
    )
    failed = await catalog.refresh()

    assert failed.get("core.clock") is not None
    assert failed.get("mcp.remote.read") is not None
    remote_status = failed.source_status("remote")
    assert remote_status.available is False
    assert secret not in (remote_status.error or "")
    assert "Bearer " not in (remote_status.error or "")
    assert "traceback" not in (remote_status.error or "").lower()

    remote.error = None
    recovered = await catalog.refresh()
    assert recovered.source_status("remote").available is True


def test_candidate_selection_is_bounded_stable_and_never_falls_back_to_all() -> None:
    snapshot = ToolCatalog.snapshot(
        revision=7,
        descriptors=(
            descriptor("core.clock", always_available=True),
            descriptor("search.web", tags=("learn", "v2.researcher")),
            descriptor("search.deep", tags=("learn",)),
            descriptor("office.write", tags=("office",)),
            descriptor("disabled.tool", enabled=False, tags=("learn",)),
            descriptor(
                "offline.tool",
                source_id="offline",
                source_kind="mcp",
                tags=("learn",),
            ),
        ),
        sources=(
            ToolSourceStatus("builtin", "builtin", available=True),
            ToolSourceStatus("offline", "mcp", available=False),
        ),
    )
    selector = ToolCandidateSelector()

    selected = selector.select(
        snapshot,
        task_type="learn",
        profile_name="v2.researcher",
        skill_names=("research",),
        requested_tools=("search.deep", "search.web", "office.write"),
        core_tools=("core.clock",),
        budget=2,
    )
    no_match = selector.select(
        snapshot,
        task_type="plan",
        profile_name="v2.planner",
        skill_names=("structured-planning",),
        requested_tools=(),
        core_tools=("core.clock",),
        budget=2,
    )

    assert selected.names == ("core.clock", "search.deep")
    assert selected.reasons == (
        ("core.clock", "core"),
        ("search.deep", "explicit_request+task"),
    )
    assert no_match.names == ("core.clock",)
    assert "office.write" not in no_match.names
    assert "disabled.tool" not in selected.names
    assert "offline.tool" not in selected.names


def test_capability_catalog_projects_one_snapshot_without_loading_handlers() -> None:
    snapshot = ToolCatalog.snapshot(
        revision=3,
        descriptors=(
            descriptor("search.web"),
            descriptor(
                "mcp.notes.read",
                source_id="notes",
                source_kind="mcp",
                enabled=False,
            ),
        ),
        sources=(
            ToolSourceStatus("builtin", "builtin", available=True),
            ToolSourceStatus("notes", "mcp", available=True),
        ),
    )

    registry = build_default_registry(
        ROOT / "backend" / "skills",
        tool_snapshot=snapshot,
    )
    tools = registry.list(kind=CapabilityKind.TOOL)

    assert registry.tool_snapshot_revision == 3
    assert [(item.id, item.enabled) for item in tools] == [
        ("tool.mcp.notes.read", False),
        ("tool.search.web", True),
    ]
    assert all("handler" not in repr(item).lower() for item in tools)


def test_planner_binds_snapshot_revision_versions_and_finite_budget() -> None:
    task = MagicMock(
        id="task-1",
        user_id="user-1",
        task_type="learn",
        input_text="/learn dynamic tools",
        model_class=None,
    )
    profile = DefaultProfileSelector().select(task)
    context = TaskContext(
        task_id="task-1",
        user_id="user-1",
        task_type="learn",
        input_text="/learn dynamic tools",
        memory_summary="",
        allowed_tools=("search.web",),
        capability_revision=9,
        tool_snapshot_revision=9,
        tool_versions=(("search.web", "search-v1"),),
    )

    plan = DefaultPlanningLayer(max_tool_count=2).build_plan(
        task=task,
        profile=profile,
        context=context,
    )

    assert plan.tool_snapshot_revision == 9
    assert plan.tool_count_budget == 2
    assert plan.tool_versions == (("search.web", "search-v1"),)
    assert len(plan.allowed_tools) + len(plan.approval_required_tools) <= 2


def test_only_planned_tool_schemas_are_exposed() -> None:
    snapshot = ToolCatalog.snapshot(
        revision=2,
        descriptors=(
            descriptor("search.web"),
            descriptor("unused.tool"),
            descriptor("disabled.tool", enabled=False),
        ),
        sources=(ToolSourceStatus("builtin", "builtin", available=True),),
    )

    schemas = build_planned_tool_schemas(
        snapshot,
        allowed_tools=("search.web",),
        approval_required_tools=(),
    )

    assert [schema["function"]["name"] for schema in schemas] == ["search.web"]
    serialized = json.dumps(schemas, ensure_ascii=False)
    assert "unused.tool" not in serialized
    assert "disabled.tool" not in serialized
    assert "source_id" not in serialized


@pytest.mark.asyncio
async def test_registry_rejects_stale_or_unplanned_and_executes_exact_version() -> None:
    calls: list[str] = []

    async def handler(invocation: ToolInvocation) -> dict[str, bool]:
        calls.append(invocation.name)
        return {"ok": True}

    session = MagicMock()
    session.flush = AsyncMock()
    registry = ToolRegistry(session=session, snapshot_revision=4)
    registry.register(
        ToolSpec(
            name="search.web",
            description="Search",
            risk_level="L1",
            handler=handler,
            version="search-v2",
            source_id="builtin",
        )
    )

    with pytest.raises(ToolNotAllowedError):
        await registry.execute(
            ToolInvocation(
                task_id="task-1",
                user_id="user-1",
                name="search.web",
                tool_snapshot_revision=4,
                tool_version="search-v2",
            ),
            allowed_tools=(),
            approval_required_tools=(),
        )
    with pytest.raises(ToolSnapshotStaleError):
        await registry.execute(
            ToolInvocation(
                task_id="task-1",
                user_id="user-1",
                name="search.web",
                tool_snapshot_revision=3,
                tool_version="search-v1",
            ),
            allowed_tools=("search.web",),
            approval_required_tools=(),
        )

    result = await registry.execute(
        ToolInvocation(
            task_id="task-1",
            user_id="user-1",
            name="search.web",
            tool_snapshot_revision=4,
            tool_version="search-v2",
        ),
        allowed_tools=("search.web",),
        approval_required_tools=(),
    )

    assert result == {"ok": True}
    assert calls == ["search.web"]
    logged = "\n".join(
        str(call.args[0].input_text) for call in session.add.call_args_list
    )
    assert '"tool_snapshot_revision":4' in logged
    assert '"tool_version":"search-v2"' in logged
