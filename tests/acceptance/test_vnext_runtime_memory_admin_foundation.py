from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from application.enterprise_runtime import (
    GovernanceAuditInput,
    GovernanceAuditService,
    EnterpriseAdminReadService,
    approval_choices,
    argument_fingerprint,
    employee_capability_summary,
    governed_semantic_search,
    knowledge_retrieval_filter,
    memory_retrieval_filter,
    require_admin_role,
    revalidate_approval,
)
from domain.models import Base, GovernanceAudit, KnowledgeDocument, Memory, Organization, Task, Tenant, User
from domain.policies.enterprise import (
    AgentDefinition,
    GovernedAgentProfile,
    GovernedRole,
    GovernanceValidationError,
    PolicyDecision,
    PolicyEffect,
    SubjectContext,
    derive_child_agent_profile,
)
from runtime.contracts import ExecutionMode, normalize_agent_event, select_execution_mode
from runtime.subagents import SubAgentCoordinator, SubAgentRequest, SubAgentResult
from tools.core.registry import ToolHandler, ToolInvocation, ToolRegistry, ToolSpec


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    """Provide isolated persistence for the VNext 06-09 acceptance slice."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'vnext-06-09.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _profile() -> GovernedAgentProfile:
    """Build a parent profile with deliberately broader authority than a child needs."""
    return GovernedAgentProfile(
        subject=SubjectContext(
            "tenant-a", "user-a", ("sales",), (GovernedRole.MEMBER,)
        ),
        capabilities=("crm.read", "crm.write", "email.send", "finance.read"),
        tools=("web.search", "crm.customer.read", "crm.customer.write", "email.send"),
        knowledge_scopes=("organization.sales", "department.research"),
        memory_access=(("personal", "read_write"), ("department", "read")),
        max_runtime_seconds=1_800,
        max_cost_usd=5.0,
    )


def test_runtime_mode_and_events_are_framework_neutral_bounded_and_redacted() -> None:
    """Ordinary work stays interactive while durable signals and event safety are stable."""
    assert select_execution_mode() is ExecutionMode.INTERACTIVE
    assert select_execution_mode(allows_subagents=True) is ExecutionMode.DURABLE
    assert select_execution_mode(checkpoint_id="checkpoint-1") is ExecutionMode.DURABLE

    event = normalize_agent_event(
        event_type="task.waiting_approval",
        task_id="task-1",
        run_id="run-1",
        tenant_id="tenant-a",
        user_id="user-a",
        payload={"subject": "email.send", "authorization": "Bearer secret", "nested": {"token": "secret"}},
    )
    assert event.type == "approval.required"
    assert event.payload["authorization"] == "[REDACTED]"
    assert event.payload["nested"] == {"token": "[REDACTED]"}
    with pytest.raises(ValueError, match="Unsupported"):
        normalize_agent_event(
            event_type="langgraph.node.raw",
            task_id="task-1",
            tenant_id="tenant-a",
            user_id="user-a",
            payload={},
        )


@pytest.mark.asyncio
async def test_subagent_receives_only_intersected_authority() -> None:
    """Delegation contracts capabilities, tools, context scopes, and budgets."""
    definition = AgentDefinition(
        id="research",
        required_capabilities=("crm.read",),
        required_tools=("web.search", "crm.customer.read"),
        required_knowledge_scopes=("department.research",),
        required_memory_access=(("department", "read"),),
        max_runtime_seconds=120,
        max_cost_usd=0.5,
    )
    child = derive_child_agent_profile(_profile(), definition)
    assert child.capabilities == ("crm.read",)
    assert child.tools == ("web.search", "crm.customer.read")
    assert child.knowledge_scopes == ("department.research",)
    assert child.memory_access == (("department", "read"),)
    assert child.max_runtime_seconds == 120
    assert child.max_cost_usd == 0.5

    captured: list[GovernedAgentProfile | None] = []

    class Runner:
        """Capture the actual request delivered by the coordinator."""

        async def run(self, request: SubAgentRequest) -> SubAgentResult:
            """Return a bounded result after observing the contracted profile."""
            captured.append(request.governed_profile)
            return SubAgentResult(request.step_index, request.role, "done")

    results = await SubAgentCoordinator(runner=Runner()).run(
        task_id="task-1",
        user_id="user-a",
        requests=(SubAgentRequest(0, "research", "research", "context"),),
        parent_profile=_profile(),
        agent_definitions={"research": definition},
    )
    assert results[0].error is None
    assert captured == [child]

    denied = await SubAgentCoordinator(runner=Runner()).run(
        task_id="task-1",
        user_id="user-a",
        requests=(SubAgentRequest(0, "finance", "approve", "context"),),
        parent_profile=_profile(),
        agent_definitions={
            "finance": AgentDefinition(
                id="finance", required_capabilities=("finance.approve",)
            )
        },
    )
    assert denied[0].error == "GovernanceValidationError"


@pytest.mark.asyncio
async def test_memory_and_knowledge_filters_are_resolved_before_semantic_search() -> None:
    """A semantic provider receives only profile-derived tenant and scope filters."""
    calls: list[Mapping[str, object]] = []

    class Provider:
        """Fake semantic provider that exposes the filter it received."""

        async def search(
            self, *, query: str, filters: Mapping[str, object], limit: int
        ) -> tuple[Mapping[str, object], ...]:
            """Capture the pre-retrieval constraints and return a safe candidate."""
            calls.append(filters)
            return ({"id": "safe"},)

    memory_filter = memory_retrieval_filter(_profile())
    knowledge_filter = knowledge_retrieval_filter(_profile())
    await governed_semantic_search(
        Provider(), query="customers", access_filter=memory_filter, limit=100
    )
    await governed_semantic_search(
        Provider(), query="playbook", access_filter=knowledge_filter, limit=5
    )
    assert calls[0]["tenant_id"] == "tenant-a"
    assert calls[0]["scopes"] == ("personal", "department")
    assert calls[1]["scopes"] == ("organization", "department")
    assert "tenant-b" not in str(calls)


def test_employee_admin_and_approval_boundaries_fail_closed() -> None:
    """Employee views are read-only and approval options remain policy-derived."""
    profile = _profile()
    summary = employee_capability_summary(
        profile, approval_required_tools=("email.send", "unknown")
    )
    assert summary.approval_required_tools == ("email.send",)
    assert approval_choices(PolicyDecision(PolicyEffect.CONFIRM, "risk")) == (
        "approve_once",
        "reject",
    )
    assert approval_choices(
        PolicyDecision(PolicyEffect.APPROVAL, "policy"), persistent_allowed=True
    ) == ("approve_once", "reject", "allow_similar")
    with pytest.raises(GovernanceValidationError, match="Administrative"):
        require_admin_role(profile.subject, GovernedRole.ENTERPRISE_ADMIN)

    args = {"recipient": "outside@example.com", "subject": "Brief"}
    fingerprint = argument_fingerprint(args)
    revalidate_approval(
        expected_fingerprint=fingerprint,
        arguments=args,
        decision=PolicyDecision(PolicyEffect.ALLOW, "approved"),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    with pytest.raises(GovernanceValidationError, match="changed"):
        revalidate_approval(
            expected_fingerprint=fingerprint,
            arguments={**args, "recipient": "attacker@example.com"},
            decision=PolicyDecision(PolicyEffect.ALLOW, "approved"),
        )


@pytest.mark.asyncio
async def test_governed_metadata_defaults_and_audit_are_private_redacted_and_correlated(
    sessionmaker: async_sessionmaker,
) -> None:
    """Additive persistence retains private ownership and required redacted audit facts."""
    async with sessionmaker() as session:
        tenant = Tenant(id="tenant-a", name="Tenant A", status="active")
        user = User(id="user-a", tenant_id="tenant-a", display_name="User A")
        session.add_all((tenant, user))
        await session.flush()
        memory = Memory(user_id=user.id, owner_id=user.id, content="pref")
        document = KnowledgeDocument(
            user_id=user.id,
            owner_id=user.id,
            source_label="guide",
            source_path="guide.md",
            media_type="text/markdown",
            checksum="a" * 64,
            parser_version="1",
            status="ready",
        )
        session.add_all((memory, document))
        await session.flush()
        audit = await GovernanceAuditService(session).record(
            GovernanceAuditInput(
                tenant_id=tenant.id,
                subject_id=user.id,
                organization_id="sales",
                task_id="task-1",
                run_id="run-1",
                capability_key="email",
                tool_key="email.send",
                provider_key="smtp",
                policy_decision="CONFIRM",
                result_status="approved",
                arguments_hash=argument_fingerprint({"recipient": "outside@example.com"}),
                summary="Authorization: Bearer reusable-secret",
            )
        )
        await session.commit()

        assert memory.visibility == "private" and memory.tenant_id == "local"
        assert document.visibility == "private" and document.classification == "internal"
        stored = await session.scalar(select(GovernanceAudit).where(GovernanceAudit.id == audit.id))
        assert stored is not None
        assert stored.summary == "[REDACTED]"
        assert stored.task_id == "task-1" and stored.run_id == "run-1"
        assert "reusable-secret" not in stored.summary


def test_additive_migration_backfills_owner_and_keeps_worker_payload_framework_neutral() -> None:
    """The migration is additive and worker execution does not queue framework commands."""
    root = Path(__file__).resolve().parents[2]
    migration = (root / "backend/migrations/versions/202608100003_vnext_runtime_memory_audit.py").read_text()
    worker = (root / "backend/workers/runtime.py").read_text()
    assert "owner_id = user_id" in migration
    assert 'server_default="private"' in migration
    assert "execution_mode" in migration
    assert "graph.invoke" not in worker
    assert "Command(" not in worker


@pytest.mark.asyncio
async def test_admin_reads_are_role_scoped_and_tool_decisions_write_required_audit(
    sessionmaker: async_sessionmaker,
) -> None:
    """Admin reads require roles and ToolRegistry persists audit without telemetry."""
    async with sessionmaker() as session:
        tenant = Tenant(id="tenant-a", name="Tenant A", status="active")
        organization = Organization(
            id="sales", tenant_id=tenant.id, name="Sales", type="department", status="active"
        )
        user = User(id="user-a", tenant_id=tenant.id, display_name="User A")
        task = Task(
            id="task-1",
            user_id=user.id,
            tenant_id=tenant.id,
            organization_id=organization.id,
            owner_id=user.id,
            platform="api",
            task_type="office",
            input_text="read",
            status="running",
        )
        session.add_all((tenant, organization, user, task))
        await session.flush()

        auditor = SubjectContext(
            tenant.id, user.id, (organization.id,), (GovernedRole.AUDITOR,)
        )
        assert (await EnterpriseAdminReadService(session).list_organizations(auditor))[0]["id"] == "sales"

        async def handler(_invocation: ToolInvocation) -> dict[str, bool]:
            """Return a deterministic result for required audit verification."""
            return {"ok": True}

        registry = ToolRegistry(session=session)
        registry.register(
            ToolSpec(
                name="demo.read",
                description="Read demo",
                risk_level="L0",
                handler=cast(ToolHandler, handler),
                input_schema={"type": "object", "additionalProperties": False},
            )
        )
        await registry.execute(
            ToolInvocation(task.id, user.id, "demo.read", {}),
            allowed_tools=("demo.read",),
            approval_required_tools=(),
        )
        audits = tuple(
            await session.scalars(
                select(GovernanceAudit).where(GovernanceAudit.task_id == task.id)
            )
        )
        assert audits and audits[-1].tool_key == "demo.read"
        assert audits[-1].arguments_hash and audits[-1].summary == ""
