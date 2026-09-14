from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routers.enterprise import (
    get_my_capabilities,
    list_admin_organizations,
)
from app.api.schemas.tasks import approval_response
from app.support.errors import AppError
from application.enterprise_governance import (
    EnterpriseGovernanceService,
    GovernedAgentProfileResolver,
    SqlAlchemyToolPolicyAuthorizer,
    resolve_local_subject,
)
from application.enterprise_resources import ConnectorService, SkillCapabilityService
from application.enterprise_runtime import (
    argument_fingerprint,
    employee_capability_summary,
    governed_semantic_search,
    knowledge_retrieval_filter,
    memory_retrieval_filter,
)
from application.task_execution.lifecycle import (
    ApprovalDecisionConflictError,
    ApprovalService,
)
from application.vnext_readiness import (
    ReadinessGateEvidence,
    ReadinessGateStatus,
    evaluate_vnext_readiness,
)
from domain.models import (
    AgentRun,
    Approval,
    ApprovalStatus,
    ApprovalType,
    Base,
    CapabilityDefinition,
    CapabilityVersion,
    ConnectorTool,
    Conversation,
    GovernanceAudit,
    KnowledgeDocument,
    Memory,
    Organization,
    OrganizationMembership,
    Task,
    TaskEvent,
    Tenant,
    User,
)
from domain.policies.enterprise import (
    GovernanceValidationError,
    GovernedRole,
    PolicyEffect,
)
from infrastructure.repositories import ApprovalRepository
from tools import ArtifactStore
from tools.gateway import ToolGateway
from tools.core.registry import (
    ToolHandler,
    ToolApprovalRequiredError,
    ToolExecutionError,
    ToolInvocation,
    ToolNotAllowedError,
    ToolRegistry,
    ToolSpec,
)


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    """Provide isolated persistence for the VNext 10-11 release gates."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'vnext-10-11.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@dataclass(frozen=True)
class FinanceFixture:
    """Stable identifiers for the deterministic Finance governance scenario."""

    tenant_id: str
    root_id: str
    finance_id: str
    sales_id: str
    publisher_id: str
    employee_id: str
    manager_id: str
    director_id: str
    sales_user_id: str
    capability_id: str
    capability_version_id: str
    reviewed_tool_ids: tuple[str, ...]


class FinanceDiscoveryClient:
    """Deterministic provider metadata used only by the acceptance fixture."""

    async def test_connection(self) -> None:
        """Represent a reachable in-process provider."""

    async def list_tools(self) -> tuple[dict[str, object], ...]:
        """Return untrusted external names for explicit administrative review."""
        return tuple(
            {
                "name": external,
                "description": internal,
                "version": "1",
                "input_schema": {"type": "object", "properties": {}},
            }
            for external, internal in (
                ("read_expense", "expense.read"),
                ("assess_expense", "expense.assess"),
                ("approve_expense", "expense.approve"),
            )
        )


async def _build_finance_fixture(session: AsyncSession) -> FinanceFixture:
    """Create governed organization, publication, Connector, grant, and policy facts."""
    tenant = Tenant(id="tenant-finance", name="Example Enterprise", status="active")
    users = {
        "publisher": User(
            id="user-publisher", tenant_id=tenant.id, display_name="Publisher"
        ),
        "employee": User(
            id="user-finance-employee", tenant_id=tenant.id, display_name="Employee"
        ),
        "manager": User(
            id="user-finance-manager", tenant_id=tenant.id, display_name="Manager"
        ),
        "director": User(
            id="user-finance-director", tenant_id=tenant.id, display_name="Director"
        ),
        "sales": User(
            id="user-sales", tenant_id=tenant.id, display_name="Sales User"
        ),
    }
    session.add_all((tenant, *users.values()))
    await session.flush()

    governance = EnterpriseGovernanceService(session)
    root = await governance.create_organization(
        tenant_id=tenant.id,
        name="Example Enterprise",
        organization_type="enterprise",
    )
    finance = await governance.create_organization(
        tenant_id=tenant.id,
        name="Finance",
        parent_id=root.id,
    )
    sales = await governance.create_organization(
        tenant_id=tenant.id,
        name="Sales",
        parent_id=root.id,
    )
    for organization_id, user_key, role in (
        (root.id, "publisher", GovernedRole.ENTERPRISE_ADMIN),
        (finance.id, "employee", GovernedRole.MEMBER),
        (finance.id, "manager", GovernedRole.MANAGER),
        (finance.id, "director", GovernedRole.DEPARTMENT_ADMIN),
        (sales.id, "sales", GovernedRole.MEMBER),
    ):
        await governance.add_membership(
            tenant_id=tenant.id,
            organization_id=organization_id,
            user_id=users[user_key].id,
            role=role,
        )

    publisher = await resolve_local_subject(session, users["publisher"].id)
    capability = CapabilityDefinition(
        tenant_id=tenant.id,
        key="finance.expense-review",
        version="1.0.0",
        display_name="Expense Review",
        summary="Read, assess, and govern expense decisions.",
        bindings_json=json.dumps(
            {
                "tools": ["expense.read", "expense.assess", "expense.approve"],
                "knowledge_scopes": ["organization.finance"],
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        status="active",
    )
    session.add(capability)
    await session.flush()
    capability_version = await SkillCapabilityService(session).publish_capability(
        subject=publisher,
        capability_id=capability.id,
        version="1.0.0",
        skill_references=(),
        tools=("expense.read", "expense.assess", "expense.approve"),
        knowledge=("organization.finance",),
        default_risk="R3",
    )
    director = await resolve_local_subject(session, users["director"].id)
    await SkillCapabilityService(session).validate_distribution(
        subject=director,
        capability_version_id=capability_version.id,
        organization_id=finance.id,
    )
    await governance.create_grant(
        tenant_id=tenant.id,
        source_organization_id=root.id,
        target_organization_id=root.id,
        capability_id=capability.id,
        version_constraint="1.0.0",
        can_delegate=True,
        max_delegate_depth=2,
        constraints={"amount_max": 500_000},
        bootstrap=True,
    )
    await governance.create_grant(
        tenant_id=tenant.id,
        source_organization_id=root.id,
        target_organization_id=finance.id,
        capability_id=capability.id,
        version_constraint="1.0.0",
        can_delegate=True,
        max_delegate_depth=1,
        constraints={"amount_max": 500_000},
    )

    connector_service = ConnectorService(session)
    connector = await connector_service.create_connector(
        subject=publisher,
        key="finance-expenses",
        connector_type="MCP",
        display_name="Finance Expenses",
    )
    instance = await connector_service.create_instance(
        subject=publisher,
        connector_id=connector.id,
        organization_id=finance.id,
        auth_mode="managed",
    )
    discovery = FinanceDiscoveryClient()
    await connector_service.test_instance(
        subject=publisher,
        instance_id=instance.id,
        client=discovery,
    )
    discovered = await connector_service.sync_tools(
        subject=publisher,
        instance_id=instance.id,
        client=discovery,
    )
    internal_keys = {
        "read_expense": ("expense.read", "R0"),
        "assess_expense": ("expense.assess", "R1"),
        "approve_expense": ("expense.approve", "R3"),
    }
    reviewed: list[ConnectorTool] = []
    for tool in discovered:
        internal_key, risk = internal_keys[tool.external_name]
        reviewed.append(
            await connector_service.review_tool(
                subject=publisher,
                tool_id=tool.id,
                internal_tool_key=internal_key,
                risk_level=risk,
                enabled=True,
            )
        )

    for scope_type, scope_id, effect, constraints, priority in (
        ("role", "member", PolicyEffect.DENY, {}, 100),
        (
            "role",
            "manager",
            PolicyEffect.CONFIRM,
            {"amount": {"op": "lte", "value": 50_000}},
            10,
        ),
        (
            "role",
            "manager",
            PolicyEffect.APPROVAL,
            {"amount": {"op": "gte", "value": 50_001}},
            10,
        ),
        (
            "role",
            "manager",
            PolicyEffect.DENY,
            {"amount": {"op": "gte", "value": 500_001}},
            100,
        ),
        (
            "role",
            "department_admin",
            PolicyEffect.CONFIRM,
            {"amount": {"op": "lte", "value": 500_000}},
            10,
        ),
    ):
        await governance.create_policy_rule(
            tenant_id=tenant.id,
            scope_type=scope_type,
            scope_id=scope_id,
            action="tool.execute:expense.approve",
            resource_type="tool",
            effect=effect,
            constraints=constraints,
            priority=priority,
        )
    await session.flush()
    return FinanceFixture(
        tenant.id,
        root.id,
        finance.id,
        sales.id,
        users["publisher"].id,
        users["employee"].id,
        users["manager"].id,
        users["director"].id,
        users["sales"].id,
        capability.id,
        capability_version.id,
        tuple(tool.id for tool in reviewed),
    )


async def _create_finance_task(
    session: AsyncSession,
    *,
    fixture: FinanceFixture,
    user_id: str,
    organization_id: str | None = None,
) -> tuple[Task, AgentRun]:
    """Create a task/run and persist its current server-resolved profile."""
    task = Task(
        user_id=user_id,
        tenant_id=fixture.tenant_id,
        organization_id=organization_id or fixture.finance_id,
        owner_type="user",
        owner_id=user_id,
        visibility="private",
        platform="electron",
        task_type="office",
        input_text="review expense",
        status="running",
    )
    session.add(task)
    await session.flush()
    await GovernedAgentProfileResolver(session).resolve_for_task(task)
    run = AgentRun(
        task_id=task.id,
        user_id=user_id,
        attempt_no=1,
        status="running",
        execution_mode="durable",
    )
    session.add(run)
    await session.flush()
    return task, run


def _finance_registry(
    session: AsyncSession,
    calls: list[tuple[str, dict[str, object]]],
) -> ToolRegistry:
    """Build the real governed registry with deterministic provider spies."""

    async def provider(invocation: ToolInvocation) -> dict[str, object]:
        calls.append((invocation.name, dict(invocation.arguments)))
        return {"ok": True, "tool": invocation.name}

    registry = ToolRegistry(
        session=session,
        policy_authorizer=SqlAlchemyToolPolicyAuthorizer(session),
    )
    registry.register(
        ToolSpec(
            name="expense.read",
            description="Read an expense",
            risk_level="R0",
            handler=provider,
            source_id="finance-expenses",
            input_schema={
                "type": "object",
                "properties": {"expense_id": {"type": "string"}},
                "required": ["expense_id"],
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        ToolSpec(
            name="expense.assess",
            description="Assess an expense",
            risk_level="R1",
            handler=provider,
            source_id="finance-expenses",
            input_schema={
                "type": "object",
                "properties": {"expense_id": {"type": "string"}},
                "required": ["expense_id"],
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        ToolSpec(
            name="expense.approve",
            description="Approve an expense",
            risk_level="R3",
            handler=provider,
            source_id="finance-expenses",
            input_schema={
                "type": "object",
                "properties": {
                    "expense_id": {"type": "string"},
                    "amount": {"type": "number"},
                    "idempotency_key": {"type": "string"},
                },
                "required": ["expense_id", "amount", "idempotency_key"],
                "additionalProperties": False,
            },
        )
    )
    return registry


def test_vnext_readiness_fails_closed_for_missing_or_deferred_gates() -> None:
    """Overall readiness never inherits completion from partial evidence."""
    report = evaluate_vnext_readiness(
        required_gates=("compatibility", "finance_e2e", "web_admin"),
        evidence={
            "compatibility": ReadinessGateEvidence(
                ReadinessGateStatus.PASSED,
                "stable identifiers and additive migrations verified",
            ),
            "web_admin": ReadinessGateEvidence(
                ReadinessGateStatus.DEFERRED,
                "standalone governed Web Admin is not implemented",
            ),
        },
    )

    assert report.gates["finance_e2e"].status is ReadinessGateStatus.FAILED
    assert report.gates["finance_e2e"].detail == "missing_current_evidence"
    assert report.gates["web_admin"].status is ReadinessGateStatus.DEFERRED
    assert report.complete is False


def test_vnext_readiness_is_complete_only_with_current_passed_evidence() -> None:
    """Every required gate must carry explicit current passing evidence."""
    report = evaluate_vnext_readiness(
        required_gates=("compatibility", "finance_e2e"),
        evidence={
            "compatibility": ReadinessGateEvidence(
                ReadinessGateStatus.PASSED,
                "compatibility acceptance passed",
            ),
            "finance_e2e": ReadinessGateEvidence(
                ReadinessGateStatus.PASSED,
                "governed provider composition passed",
            ),
        },
    )

    assert report.complete is True


@pytest.mark.asyncio
async def test_vnext_additive_metadata_preserves_legacy_ids_and_artifact_paths(
    sessionmaker: async_sessionmaker,
    tmp_path: Path,
) -> None:
    """Legacy owner records retain stable identities while gaining governed metadata."""
    async with sessionmaker() as session:
        tenant = Tenant(id="tenant-legacy", name="Legacy", status="active")
        organization = Organization(
            id="organization-legacy",
            tenant_id=tenant.id,
            name="Legacy Department",
            type="department",
            status="active",
        )
        user = User(
            id="user-legacy",
            tenant_id=tenant.id,
            display_name="Legacy User",
        )
        conversation = Conversation(
            id="conversation-legacy",
            user_id=user.id,
            title="Existing conversation",
            channel="electron",
        )
        task = Task(
            id="task-legacy",
            user_id=user.id,
            tenant_id=tenant.id,
            organization_id=organization.id,
            owner_type="user",
            owner_id=user.id,
            visibility="private",
            platform="electron",
            task_type="office",
            input_text="existing task",
            status="waiting_approval",
            conversation_id=conversation.id,
        )
        run = AgentRun(
            id="run-legacy",
            task_id=task.id,
            user_id=user.id,
            attempt_no=1,
            status="waiting_approval",
            checkpoint_id="checkpoint-legacy",
            execution_mode="durable",
        )
        event = TaskEvent(
            id="event-legacy",
            task_id=task.id,
            user_id=user.id,
            tenant_id=tenant.id,
            run_id=run.id,
            event_type="task.waiting_approval",
            normalized_event_type="approval.required",
            sequence=1,
            payload_json='{"status":"waiting_approval"}',
        )
        approval = Approval(
            id="approval-legacy",
            task_id=task.id,
            status="pending",
            tool_name="legacy.tool",
            subject="legacy.tool",
            request_fingerprint="a" * 64,
            expires_at=None,
        )
        memory = Memory(
            id="memory-legacy",
            user_id=user.id,
            tenant_id=tenant.id,
            organization_id=organization.id,
            owner_id=user.id,
            visibility="private",
            content="existing memory",
        )
        document = KnowledgeDocument(
            id="knowledge-legacy",
            user_id=user.id,
            tenant_id=tenant.id,
            organization_id=organization.id,
            owner_id=user.id,
            visibility="private",
            classification="internal",
            source_label="existing guide",
            source_path="knowledge/existing.md",
            media_type="text/markdown",
            checksum="b" * 64,
            parser_version="1",
            status="ready",
        )
        session.add_all(
            (
                tenant,
                organization,
                user,
                conversation,
                task,
                run,
                event,
                approval,
                memory,
                document,
            )
        )
        await session.commit()
        session.expunge_all()

        assert (await session.get(Task, "task-legacy")).conversation_id == (
            "conversation-legacy"
        )
        assert (await session.get(AgentRun, "run-legacy")).checkpoint_id == (
            "checkpoint-legacy"
        )
        stored_event = await session.get(TaskEvent, "event-legacy")
        assert stored_event.event_type == "task.waiting_approval"
        assert stored_event.normalized_event_type == "approval.required"
        assert (await session.get(Approval, "approval-legacy")).task_id == task.id
        assert (await session.get(Memory, "memory-legacy")).owner_id == user.id
        assert (await session.get(KnowledgeDocument, "knowledge-legacy")).source_path == (
            "knowledge/existing.md"
        )

    store = ArtifactStore(tmp_path / "artifacts")
    target = store.reserve(
        task_id="task-legacy",
        filename="existing-report.md",
        suffix=".md",
    )
    store.atomic_write_bytes(target, b"existing artifact")
    artifact = store.describe(target, media_type="text/markdown")
    assert artifact.reference == "task-legacy/existing-report.md"
    assert store.read_bytes(
        task_id="task-legacy", reference=artifact.reference
    ) == b"existing artifact"


def test_vnext_migrations_and_worker_payload_remain_additive_and_neutral() -> None:
    """VNext revisions are linear/reversible and never queue framework commands."""
    root = Path(__file__).resolve().parents[2]
    migrations = (
        (
            root
            / "backend/migrations/versions/202608100001_vnext_identity_policy_profile.py"
        ),
        (
            root
            / "backend/migrations/versions/202608100002_vnext_capability_connector_credential.py"
        ),
        (
            root
            / "backend/migrations/versions/202608100003_vnext_runtime_memory_audit.py"
        ),
        (
            root
            / "backend/migrations/versions/202608100004_vnext_release_approval_binding.py"
        ),
    )
    expected_chain = (
        ("202608100001", "202607210003"),
        ("202608100002", "202608100001"),
        ("202608100003", "202608100002"),
        ("202608100004", "202608100003"),
    )
    for path, (revision, down_revision) in zip(
        migrations, expected_chain, strict=True
    ):
        source = path.read_text(encoding="utf-8")
        upgrade_source = source.split("def downgrade", maxsplit=1)[0]
        assert f'revision: str = "{revision}"' in source
        assert f'down_revision: str | None = "{down_revision}"' in source
        assert "def upgrade()" in source and "def downgrade()" in source
        assert "op.drop_" not in upgrade_source
        assert "DELETE FROM" not in upgrade_source
        assert "TRUNCATE" not in upgrade_source
        assert "artifacts_root" not in source

    runtime = (root / "backend/workers/runtime.py").read_text(encoding="utf-8")
    events = (
        root / "backend/application/task_execution/events.py"
    ).read_text(encoding="utf-8")
    assert "async def execute_task_by_id(\n    task_id: str," in runtime
    assert "Command(" not in runtime and "graph.invoke" not in runtime
    assert '"type": item.event_type' in events
    assert '"normalized_type": item.normalized_event_type or item.event_type' in events
    assert '"run_id": item.run_id' in events


@pytest.mark.asyncio
async def test_finance_fixture_publishes_distributes_and_reviews_server_owned_ability(
    sessionmaker: async_sessionmaker,
) -> None:
    """The Finance ability is published, distributed, and provider-reviewed server-side."""
    async with sessionmaker() as session:
        fixture = await _build_finance_fixture(session)
        version = await session.get(CapabilityVersion, fixture.capability_version_id)
        reviewed = tuple(
            await session.scalars(
                select(ConnectorTool).where(
                    ConnectorTool.id.in_(fixture.reviewed_tool_ids)
                )
            )
        )
        employee_task, _ = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.employee_id,
        )
        manager_task, _ = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        employee_profile = await GovernedAgentProfileResolver(session).resolve_for_task(
            employee_task
        )
        manager_profile = await GovernedAgentProfileResolver(session).resolve_for_task(
            manager_task
        )

        assert version is not None and version.version == "1.0.0"
        assert {tool.internal_tool_key for tool in reviewed} == {
            "expense.read",
            "expense.assess",
            "expense.approve",
        }
        assert all(tool.enabled and tool.risk_level for tool in reviewed)
        for profile in (employee_profile, manager_profile):
            assert profile.capabilities == ("finance.expense-review@1.0.0",)
            assert profile.tools == (
                "expense.approve",
                "expense.assess",
                "expense.read",
            )


@pytest.mark.asyncio
async def test_finance_employee_is_denied_approval_before_provider_execution(
    sessionmaker: async_sessionmaker,
) -> None:
    """An employee may read and assess but cannot forge expense approval authority."""
    async with sessionmaker() as session:
        fixture = await _build_finance_fixture(session)
        task, _ = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.employee_id,
        )
        calls: list[tuple[str, dict[str, object]]] = []
        registry = _finance_registry(session, calls)
        await registry.execute(
            ToolInvocation(
                task.id,
                fixture.employee_id,
                "expense.read",
                {"expense_id": "expense-1"},
            ),
            allowed_tools=("expense.read", "expense.assess", "expense.approve"),
            approval_required_tools=(),
        )
        arguments = {
            "expense_id": "expense-1",
            "amount": 20_000,
            "idempotency_key": "employee-forgery",
        }
        decision = await SqlAlchemyToolPolicyAuthorizer(session).authorize(
            ToolInvocation(
                task.id,
                fixture.employee_id,
                "expense.approve",
                arguments,
            ),
            risk_level="R3",
        )
        with pytest.raises(ToolNotAllowedError, match="current policy"):
            await registry.execute(
                ToolInvocation(
                    task.id,
                    fixture.employee_id,
                    "expense.approve",
                    arguments,
                ),
                allowed_tools=("expense.read", "expense.assess", "expense.approve"),
                approval_required_tools=(),
            )

        assert decision.effect is PolicyEffect.DENY
        assert calls == [("expense.read", {"expense_id": "expense-1"})]


async def _pending_expense_approval(
    session: AsyncSession,
    *,
    task: Task,
    run: AgentRun,
    arguments: dict[str, object],
    effect: PolicyEffect,
) -> Approval:
    """Persist exact bounded evidence after the registry requests a human gate."""
    fingerprint = argument_fingerprint(arguments)
    approval = await ApprovalRepository(session).create_pending_request(
        task_id=task.id,
        approval_type=ApprovalType.TOOL.value,
        subject="expense.approve",
        tool_name="expense.approve",
        request_summary="Approve one bounded expense request.",
        request_fingerprint=fingerprint,
        capability_key="finance.expense-review",
        policy_decision=effect.value,
        allowed_decisions_json='["approve_once","reject"]',
        run_id=run.id,
    )
    approval.expires_at = datetime.now(UTC) + timedelta(minutes=5)
    task.status = "waiting_approval"
    await session.flush()
    return approval


@pytest.mark.asyncio
async def test_finance_manager_confirmation_and_director_approval_are_exact(
    sessionmaker: async_sessionmaker,
) -> None:
    """In-limit confirmation and above-limit delegated approval reauthorize once."""
    async with sessionmaker() as session:
        fixture = await _build_finance_fixture(session)
        calls: list[tuple[str, dict[str, object]]] = []

        confirm_task, confirm_run = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        confirm_arguments: dict[str, object] = {
            "expense_id": "expense-20k",
            "amount": 20_000,
            "idempotency_key": "manager-20k",
        }
        confirm_invocation = ToolInvocation(
            confirm_task.id,
            fixture.manager_id,
            "expense.approve",
            confirm_arguments,
        )
        confirm_registry = _finance_registry(session, calls)
        confirm_decision = await SqlAlchemyToolPolicyAuthorizer(session).authorize(
            confirm_invocation,
            risk_level="R3",
        )
        with pytest.raises(ToolApprovalRequiredError):
            await confirm_registry.execute(
                confirm_invocation,
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )
        confirmation = await _pending_expense_approval(
            session,
            task=confirm_task,
            run=confirm_run,
            arguments=confirm_arguments,
            effect=PolicyEffect.CONFIRM,
        )
        await ApprovalService(session).decide(
            task_id=confirm_task.id,
            approval_id=confirmation.id,
            user_id=fixture.manager_id,
            decision=ApprovalStatus.APPROVED,
        )
        await confirm_registry.execute(
            confirm_invocation,
            allowed_tools=("expense.approve",),
            approval_required_tools=(),
        )

        approval_task, approval_run = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        approval_arguments: dict[str, object] = {
            "expense_id": "expense-100k",
            "amount": 100_000,
            "idempotency_key": "manager-100k",
        }
        approval_invocation = ToolInvocation(
            approval_task.id,
            fixture.manager_id,
            "expense.approve",
            approval_arguments,
        )
        approval_registry = _finance_registry(session, calls)
        approval_decision = await SqlAlchemyToolPolicyAuthorizer(session).authorize(
            approval_invocation,
            risk_level="R3",
        )
        with pytest.raises(ToolApprovalRequiredError):
            await approval_registry.execute(
                approval_invocation,
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )
        delegated = await _pending_expense_approval(
            session,
            task=approval_task,
            run=approval_run,
            arguments=approval_arguments,
            effect=PolicyEffect.APPROVAL,
        )
        with pytest.raises(ApprovalDecisionConflictError, match="independent"):
            await ApprovalService(session).decide(
                task_id=approval_task.id,
                approval_id=delegated.id,
                user_id=fixture.manager_id,
                decision=ApprovalStatus.APPROVED,
            )
        await ApprovalService(session).decide(
            task_id=approval_task.id,
            approval_id=delegated.id,
            user_id=fixture.director_id,
            decision=ApprovalStatus.APPROVED,
        )
        await approval_registry.execute(
            approval_invocation,
            allowed_tools=("expense.approve",),
            approval_required_tools=(),
        )

        assert confirm_decision.effect is PolicyEffect.CONFIRM
        assert approval_decision.effect is PolicyEffect.APPROVAL
        assert calls == [
            ("expense.approve", confirm_arguments),
            ("expense.approve", approval_arguments),
        ]


@pytest.mark.asyncio
async def test_governed_approval_rejects_argument_task_and_run_replay(
    sessionmaker: async_sessionmaker,
) -> None:
    """Exact evidence cannot authorize changed arguments, another task, or a retry run."""
    async with sessionmaker() as session:
        fixture = await _build_finance_fixture(session)
        task, run = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        calls: list[tuple[str, dict[str, object]]] = []
        registry = _finance_registry(session, calls)
        arguments: dict[str, object] = {
            "expense_id": "expense-replay",
            "amount": 20_000,
            "idempotency_key": "original",
        }
        invocation = ToolInvocation(
            task.id, fixture.manager_id, "expense.approve", arguments
        )
        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                invocation,
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )
        approval = await _pending_expense_approval(
            session,
            task=task,
            run=run,
            arguments=arguments,
            effect=PolicyEffect.CONFIRM,
        )
        await ApprovalService(session).decide(
            task_id=task.id,
            approval_id=approval.id,
            user_id=fixture.manager_id,
            decision=ApprovalStatus.APPROVED,
        )

        changed_arguments = {**arguments, "idempotency_key": "changed"}
        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                ToolInvocation(
                    task.id,
                    fixture.manager_id,
                    "expense.approve",
                    changed_arguments,
                ),
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )

        retry = AgentRun(
            task_id=task.id,
            user_id=fixture.manager_id,
            attempt_no=2,
            status="running",
            execution_mode="durable",
        )
        session.add(retry)
        await session.flush()
        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                invocation,
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )

        other_task, _ = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                ToolInvocation(
                    other_task.id,
                    fixture.manager_id,
                    "expense.approve",
                    arguments,
                ),
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )
        assert calls == []


@pytest.mark.asyncio
async def test_governed_approval_revalidates_expiry_policy_and_approver_authority(
    sessionmaker: async_sessionmaker,
) -> None:
    """Expired, revoked, stale-policy, and out-of-range decisions never execute."""
    async with sessionmaker() as session:
        fixture = await _build_finance_fixture(session)
        calls: list[tuple[str, dict[str, object]]] = []

        expired_task, expired_run = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        expired_args: dict[str, object] = {
            "expense_id": "expense-expired",
            "amount": 20_000,
            "idempotency_key": "expired",
        }
        expired = await _pending_expense_approval(
            session,
            task=expired_task,
            run=expired_run,
            arguments=expired_args,
            effect=PolicyEffect.CONFIRM,
        )
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.flush()
        with pytest.raises(ApprovalDecisionConflictError, match="expired"):
            await ApprovalService(session).decide(
                task_id=expired_task.id,
                approval_id=expired.id,
                user_id=fixture.manager_id,
                decision=ApprovalStatus.APPROVED,
            )

        delegated_task, delegated_run = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        delegated_args: dict[str, object] = {
            "expense_id": "expense-revoked-approver",
            "amount": 100_000,
            "idempotency_key": "revoked-approver",
        }
        delegated = await _pending_expense_approval(
            session,
            task=delegated_task,
            run=delegated_run,
            arguments=delegated_args,
            effect=PolicyEffect.APPROVAL,
        )
        await ApprovalService(session).decide(
            task_id=delegated_task.id,
            approval_id=delegated.id,
            user_id=fixture.director_id,
            decision=ApprovalStatus.APPROVED,
        )
        director_membership = await session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == fixture.director_id,
                OrganizationMembership.organization_id == fixture.finance_id,
                OrganizationMembership.role == "department_admin",
            )
        )
        assert director_membership is not None
        director_membership.status = "revoked"
        await session.flush()
        with pytest.raises(ToolApprovalRequiredError):
            await _finance_registry(session, calls).execute(
                ToolInvocation(
                    delegated_task.id,
                    fixture.manager_id,
                    "expense.approve",
                    delegated_args,
                ),
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )

        stale_task, stale_run = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        stale_args: dict[str, object] = {
            "expense_id": "expense-stale-policy",
            "amount": 20_000,
            "idempotency_key": "stale-policy",
        }
        stale = await _pending_expense_approval(
            session,
            task=stale_task,
            run=stale_run,
            arguments=stale_args,
            effect=PolicyEffect.CONFIRM,
        )
        await ApprovalService(session).decide(
            task_id=stale_task.id,
            approval_id=stale.id,
            user_id=fixture.manager_id,
            decision=ApprovalStatus.APPROVED,
        )
        await EnterpriseGovernanceService(session).create_policy_rule(
            tenant_id=fixture.tenant_id,
            scope_type="enterprise",
            scope_id=None,
            action="tool.execute:unrelated.read",
            resource_type="tool",
            effect=PolicyEffect.ALLOW,
        )
        with pytest.raises(ToolNotAllowedError, match="current policy"):
            await _finance_registry(session, calls).execute(
                ToolInvocation(
                    stale_task.id,
                    fixture.manager_id,
                    "expense.approve",
                    stale_args,
                ),
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )

        out_of_range_task, _ = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        out_of_range_args: dict[str, object] = {
            "expense_id": "expense-too-large",
            "amount": 500_001,
            "idempotency_key": "too-large",
        }
        out_of_range_invocation = ToolInvocation(
            out_of_range_task.id,
            fixture.manager_id,
            "expense.approve",
            out_of_range_args,
        )
        decision = await SqlAlchemyToolPolicyAuthorizer(session).authorize(
            out_of_range_invocation,
            risk_level="R3",
        )
        with pytest.raises(ToolNotAllowedError, match="current policy"):
            await _finance_registry(session, calls).execute(
                out_of_range_invocation,
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )
        assert decision.effect is PolicyEffect.DENY
        assert calls == []


@pytest.mark.asyncio
async def test_sales_subject_cannot_discover_finance_capability_or_resources(
    sessionmaker: async_sessionmaker,
) -> None:
    """The employee/profile boundary reveals no Finance internals to Sales."""
    async with sessionmaker() as session:
        fixture = await _build_finance_fixture(session)
        sales_task, _ = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.sales_user_id,
            organization_id=fixture.sales_id,
        )
        profile = await GovernedAgentProfileResolver(session).resolve_for_task(
            sales_task
        )
        summary = employee_capability_summary(
            profile,
            approval_required_tools=("expense.approve",),
        )

        assert summary.capabilities == ()
        assert summary.tools == ()
        assert summary.approval_required_tools == ()
        assert profile.knowledge_scopes == ()


@pytest.mark.asyncio
async def test_retrieval_filters_precede_recall_and_unsupported_scope_fails_closed(
    sessionmaker: async_sessionmaker,
) -> None:
    """Provider spies receive server scope first and cannot downgrade filtering."""
    async with sessionmaker() as session:
        fixture = await _build_finance_fixture(session)
        finance_task, _ = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.employee_id,
        )
        sales_task, _ = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.sales_user_id,
            organization_id=fixture.sales_id,
        )
        finance_profile = await GovernedAgentProfileResolver(session).resolve_for_task(
            finance_task
        )
        sales_profile = await GovernedAgentProfileResolver(session).resolve_for_task(
            sales_task
        )
        finance_memory = Memory(
            id="finance-memory",
            user_id=fixture.employee_id,
            tenant_id=fixture.tenant_id,
            organization_id=fixture.finance_id,
            owner_id=fixture.employee_id,
            visibility="private",
            content="finance private memory",
        )
        finance_knowledge = KnowledgeDocument(
            id="finance-knowledge",
            user_id=fixture.employee_id,
            tenant_id=fixture.tenant_id,
            organization_id=fixture.finance_id,
            owner_id=fixture.employee_id,
            visibility="organization",
            classification="internal",
            source_label="Finance handbook",
            source_path="finance/handbook.md",
            media_type="text/markdown",
            checksum="f" * 64,
            parser_version="1",
            status="ready",
        )
        session.add_all((finance_memory, finance_knowledge))
        await session.flush()

        provider_calls: list[dict[str, object]] = []

        class ScopedProvider:
            supports_governed_filters = True

            async def search(
                self, *, query: str, filters: Mapping[str, object], limit: int
            ) -> tuple[Mapping[str, object], ...]:
                provider_calls.append(dict(filters))
                if filters["owner_id"] == fixture.employee_id:
                    return ({"id": finance_memory.id},)
                return ()

        finance_results = await governed_semantic_search(
            ScopedProvider(),
            query="expense",
            access_filter=memory_retrieval_filter(finance_profile),
            limit=5,
        )
        sales_results = await governed_semantic_search(
            ScopedProvider(),
            query="expense",
            access_filter=memory_retrieval_filter(sales_profile),
            limit=5,
        )
        sales_knowledge = await governed_semantic_search(
            ScopedProvider(),
            query="finance handbook",
            access_filter=knowledge_retrieval_filter(sales_profile),
            limit=5,
        )

        unsupported_calls: list[str] = []

        class UnsupportedProvider:
            supports_governed_filters = False

            async def search(self, **_: object) -> tuple[dict[str, object], ...]:
                unsupported_calls.append("called")
                return ({"id": finance_knowledge.id},)

        unsupported = await governed_semantic_search(
            UnsupportedProvider(),
            query="finance handbook",
            access_filter=knowledge_retrieval_filter(finance_profile),
            limit=5,
        )

        assert finance_results == ({"id": "finance-memory"},)
        assert sales_results == () and sales_knowledge == ()
        assert provider_calls[0]["tenant_id"] == fixture.tenant_id
        assert provider_calls[0]["owner_id"] == fixture.employee_id
        assert provider_calls[1]["owner_id"] == fixture.sales_user_id
        assert unsupported == () and unsupported_calls == []


@pytest.mark.asyncio
async def test_sales_forged_gateway_invocation_cannot_trust_client_authority(
    sessionmaker: async_sessionmaker,
) -> None:
    """Fabricated plan and approval records cannot widen an empty governed profile."""
    async with sessionmaker() as session:
        fixture = await _build_finance_fixture(session)
        task, run = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.sales_user_id,
            organization_id=fixture.sales_id,
        )
        arguments: dict[str, object] = {
            "expense_id": "forged",
            "amount": 20_000,
            "idempotency_key": "forged",
        }
        forged = await ApprovalRepository(session).create_pending_request(
            task_id=task.id,
            approval_type=ApprovalType.TOOL.value,
            subject="expense.approve",
            tool_name="expense.approve",
            request_summary="fabricated",
            request_fingerprint=argument_fingerprint(arguments),
            capability_key="finance.expense-review",
            policy_decision=PolicyEffect.CONFIRM.value,
            run_id=run.id,
        )
        forged.status = ApprovalStatus.APPROVED.value
        forged.decided_by_user_id = fixture.sales_user_id
        forged.decided_at = datetime.now(UTC)
        task.status = "pending"
        await session.flush()
        calls: list[tuple[str, dict[str, object]]] = []
        gateway = ToolGateway(_finance_registry(session, calls))

        with pytest.raises(ToolNotAllowedError, match="current policy"):
            await gateway.execute(
                ToolInvocation(
                    task.id,
                    fixture.sales_user_id,
                    "expense.approve",
                    arguments,
                ),
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )
        assert calls == []


@pytest.mark.asyncio
async def test_governance_audit_correlates_decisions_results_and_redacts_arguments(
    sessionmaker: async_sessionmaker,
) -> None:
    """Denied, gated, approved, successful, and failed attempts leave safe facts."""
    async with sessionmaker() as session:
        fixture = await _build_finance_fixture(session)
        employee_task, _ = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.employee_id,
        )
        calls: list[tuple[str, dict[str, object]]] = []
        employee_registry = _finance_registry(session, calls)
        denied_args: dict[str, object] = {
            "expense_id": "denied",
            "amount": 20_000,
            "idempotency_key": "denied",
        }
        with pytest.raises(ToolNotAllowedError):
            await employee_registry.execute(
                ToolInvocation(
                    employee_task.id,
                    fixture.employee_id,
                    "expense.approve",
                    denied_args,
                ),
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )

        manager_task, manager_run = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        approved_args: dict[str, object] = {
            "expense_id": "approved",
            "amount": 20_000,
            "idempotency_key": "approved-secret-value",
        }
        approved_invocation = ToolInvocation(
            manager_task.id,
            fixture.manager_id,
            "expense.approve",
            approved_args,
        )
        manager_registry = _finance_registry(session, calls)
        with pytest.raises(ToolApprovalRequiredError):
            await manager_registry.execute(
                approved_invocation,
                allowed_tools=("expense.approve",),
                approval_required_tools=(),
            )
        approval = await _pending_expense_approval(
            session,
            task=manager_task,
            run=manager_run,
            arguments=approved_args,
            effect=PolicyEffect.CONFIRM,
        )
        await ApprovalService(session).decide(
            task_id=manager_task.id,
            approval_id=approval.id,
            user_id=fixture.manager_id,
            decision=ApprovalStatus.APPROVED,
        )
        await manager_registry.execute(
            approved_invocation,
            allowed_tools=("expense.approve",),
            approval_required_tools=(),
        )

        failed_task, _ = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )

        async def failing_provider(_invocation: ToolInvocation) -> object:
            raise RuntimeError("Authorization: Bearer reusable-provider-secret")

        failing_registry = ToolRegistry(
            session=session,
            policy_authorizer=SqlAlchemyToolPolicyAuthorizer(session),
            sensitive_values=("reusable-provider-secret",),
        )
        failing_registry.register(
            ToolSpec(
                name="expense.assess",
                description="Failing assessment provider",
                risk_level="R1",
                source_id="finance-expenses",
                handler=cast(ToolHandler, failing_provider),
                input_schema={
                    "type": "object",
                    "properties": {"expense_id": {"type": "string"}},
                    "required": ["expense_id"],
                    "additionalProperties": False,
                },
            )
        )
        with pytest.raises(ToolExecutionError):
            await failing_registry.execute(
                ToolInvocation(
                    failed_task.id,
                    fixture.manager_id,
                    "expense.assess",
                    {"expense_id": "sensitive-expense-id"},
                ),
                allowed_tools=("expense.assess",),
                approval_required_tools=(),
            )

        audits = tuple(
            await session.scalars(
                select(GovernanceAudit).where(
                    GovernanceAudit.tenant_id == fixture.tenant_id
                )
            )
        )
        assert {row.policy_decision for row in audits} >= {
            "DENY",
            "CONFIRM",
            "ALLOW",
        }
        assert {row.result_status for row in audits} >= {
            "explicit_deny",
            "tool_approval_required",
            "provider_succeeded",
            "provider_failed",
        }
        success = next(
            row for row in audits if row.result_status == "provider_succeeded"
        )
        assert success.task_id == manager_task.id
        assert success.run_id == manager_run.id
        assert success.capability_key == "finance.expense-review"
        assert success.tool_key == "expense.approve"
        assert success.provider_key == "finance-expenses"
        assert success.approval_id == approval.id
        assert success.arguments_hash == argument_fingerprint(approved_args)
        serialized = json.dumps(
            [row.__dict__ for row in audits],
            ensure_ascii=False,
            default=str,
        )
        assert "approved-secret-value" not in serialized
        assert "reusable-provider-secret" not in serialized
        assert "sensitive-expense-id" not in serialized


@pytest.mark.asyncio
async def test_employee_api_is_read_only_and_admin_mutations_use_server_roles(
    sessionmaker: async_sessionmaker,
) -> None:
    """Employee capability/approval views cannot manufacture management authority."""
    async with sessionmaker() as session:
        fixture = await _build_finance_fixture(session)
        employee_subject = await resolve_local_subject(session, fixture.employee_id)
        capabilities = await get_my_capabilities(
            subject=employee_subject,
            session=session,
        )
        assert capabilities.capabilities == ["finance.expense-review@1.0.0"]
        assert capabilities.tools == [
            "expense.approve",
            "expense.assess",
            "expense.read",
        ]
        assert [detail.id for detail in capabilities.capability_details] == [
            "finance.expense-review@1.0.0"
        ]
        assert capabilities.capability_details[0].display_name == "Expense Review"

        task, run = await _create_finance_task(
            session,
            fixture=fixture,
            user_id=fixture.manager_id,
        )
        arguments: dict[str, object] = {
            "expense_id": "api-choice",
            "amount": 20_000,
            "idempotency_key": "api-choice",
        }
        approval = await _pending_expense_approval(
            session,
            task=task,
            run=run,
            arguments=arguments,
            effect=PolicyEffect.CONFIRM,
        )
        response = approval_response(approval)
        assert response.allowed_decisions == ["approve_once", "reject"]
        assert response.capability_key == "finance.expense-review"
        assert response.policy_decision == "CONFIRM"

        with pytest.raises(AppError) as forbidden:
            await list_admin_organizations(
                subject=employee_subject,
                session=session,
            )
        assert forbidden.value.status_code == 403

        with pytest.raises(GovernanceValidationError, match="management role"):
            await SkillCapabilityService(session).publish_capability(
                subject=employee_subject,
                capability_id=fixture.capability_id,
                version="2.0.0",
                skill_references=(),
                tools=("expense.approve",),
            )

        publisher_subject = await resolve_local_subject(session, fixture.publisher_id)
        admin = await list_admin_organizations(
            subject=publisher_subject,
            session=session,
        )
        assert {item["id"] for item in admin.items} >= {
            fixture.root_id,
            fixture.finance_id,
            fixture.sales_id,
        }


def test_electron_contract_refreshes_read_only_capabilities_without_admin_forms() -> None:
    """The compiled employee client contains reads and no enterprise mutation surface."""
    root = Path(__file__).resolve().parents[2]
    api_source = (
        root / "frontend/desktop/src/renderer/api.ts"
    ).read_text(encoding="utf-8")
    app_source = (
        root / "frontend/desktop/src/renderer/App.tsx"
    ).read_text(encoding="utf-8")
    panel_source = (
        root / "frontend/desktop/src/renderer/EffectiveCapabilitiesPanel.tsx"
    ).read_text(encoding="utf-8")

    assert "async myCapabilities(): Promise<MyCapabilities>" in api_source
    assert "/api/me/capabilities?user_id=" in api_source
    assert ".myCapabilities()" in app_source
    assert "[api, hasConfiguredUser]" in app_source
    assert 'settingsCategory === "capabilities"' in app_source
    assert "EffectiveCapabilitiesPanel" in app_source
    assert "profile={myCapabilities}" in app_source
    assert "当前已获得的工作能力" in panel_source
    assert "本机使用偏好不会扩大你的企业权限" in app_source
    assert "/api/admin/" not in api_source
    for forbidden in (
        "publishSkill(",
        "publishCapability(",
        "distributeCapability(",
        "createConnector(",
        "registerMcp(",
        "storeCredential(",
        "createPolicy(",
    ):
        assert forbidden not in api_source
        assert forbidden not in app_source
        assert forbidden not in panel_source


def test_management_renderer_uses_role_scoped_collections_and_bounded_actions() -> None:
    """The management client mirrors role scope without replacing server authority."""
    root = Path(__file__).resolve().parents[2]
    app_source = (
        root / "frontend/desktop/src/renderer/AdminApp.tsx"
    ).read_text(encoding="utf-8")
    access_source = (
        root / "frontend/desktop/src/renderer/admin-access.ts"
    ).read_text(encoding="utf-8")
    api_source = (
        root / "frontend/desktop/src/renderer/admin-api.ts"
    ).read_text(encoding="utf-8")

    for role in (
        "enterprise_admin",
        "department_admin",
        "capability_publisher",
        "connector_admin",
        "auditor",
    ):
        assert role in access_source
    for guarded_read in (
        "access.readOrganizations ? api.organizations()",
        "access.readMembers ? api.members()",
        "access.readCapabilities ? api.capabilities()",
        "access.readAudit ? api.audit()",
        "access.readCapabilityRequests ? api.capabilityRequests()",
        "access.readNodes ? api.nodes()",
        "access.readDiagnostics ? api.diagnosticPackages()",
        "access.manageNodeOperations ? api.nodeOperations()",
    ):
        assert guarded_read in app_source
    assert "if (!access.enterWorkbench)" in app_source
    assert "revision !== refreshRevision.current" in app_source
    assert "setDashboard(emptyDashboard())" in app_source
    assert "recordSkillCandidate" in api_source
    assert "/api/admin/skill-candidates" in api_source
    assert "/api/admin/capability-requests" in api_source
    assert "updateCapabilityRequestStatus" in api_source
    assert "createNodeEnrollment" in api_source
    assert "createNodeOperation" in api_source
    assert 'option value="MCP"' in app_source
    assert "credential_ref" not in app_source


def test_capability_center_exposes_governed_it_request_intake() -> None:
    """Employee requests remain separate from effective capability authority."""
    root = Path(__file__).resolve().parents[2]
    app_source = (root / "frontend/desktop/src/renderer/App.tsx").read_text(encoding="utf-8")
    api_source = (root / "frontend/desktop/src/renderer/api.ts").read_text(encoding="utf-8")
    panel_source = (
        root / "frontend/desktop/src/renderer/CapabilityRequestPanel.tsx"
    ).read_text(encoding="utf-8")

    assert "CapabilityRequestPanel" in app_source
    assert "listCapabilityRequests" in api_source
    assert "createCapabilityRequest" in api_source
    assert "/api/me/capability-requests" in api_source
    assert "向 IT 申请能力" in panel_source
    assert "不会立即扩大你的工作权限" in panel_source
    for forbidden in ("publishCapability", "distributeCapability", "createConnector"):
        assert forbidden not in panel_source


def test_management_node_operations_are_pull_based_redacted_and_whitelisted() -> None:
    """The IT surface exposes finite recovery actions without a general remote shell."""
    root = Path(__file__).resolve().parents[2]
    panel_source = (
        root / "frontend/desktop/src/renderer/NodeOperationsPanel.tsx"
    ).read_text(encoding="utf-8")
    contract_source = (
        root / "frontend/desktop/src/renderer/node-operations.ts"
    ).read_text(encoding="utf-8")
    backend_source = (
        root / "backend/application/department_node_operations.py"
    ).read_text(encoding="utf-8")

    for operation in (
        "refresh_config", "resync_config", "pause_new_work", "resume_new_work",
        "stop_task", "restart_agent",
    ):
        assert operation in contract_source
        assert operation in backend_source
    assert "不提供 Shell、远程桌面、文件浏览或任意命令" in panel_source
    assert "员工输入、凭证、环境变量或文件内容" in panel_source
    assert "command_text" not in contract_source
    assert "shell_command" not in backend_source
