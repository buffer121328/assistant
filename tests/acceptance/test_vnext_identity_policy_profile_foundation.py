from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from application.enterprise_governance import (
    EnterpriseGovernanceService,
    GovernedAgentProfileResolver,
    resolve_local_subject,
)
from application.task_execution.executor import TaskExecutionService
from domain.policies.enterprise import (
    CapabilityGrantView,
    GovernanceValidationError,
    GovernedRole,
    PolicyDecision,
    PolicyEffect,
    PolicyStatement,
    ResourceContext,
    ResourceScope,
    ResourceVisibility,
    SubjectContext,
    authorize,
    default_risk_effect,
    normalize_tool_risk,
    resource_scope_allows,
    validate_delegation,
    verified_subject,
)
from domain.models import (
    Base,
    CapabilityDefinition,
    Organization,
    Task,
    Tenant,
    User,
)
from infrastructure.repositories import TaskCreate, TaskRepository
from tools.core.registry import (
    ToolInvocation,
    ToolNotAllowedError,
    ToolRegistry,
    ToolSpec,
)


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    """Provide isolated enterprise-governance persistence for acceptance scenarios."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'vnext.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def test_subject_and_resource_scope_fail_closed_across_owner_and_tenant() -> None:
    """Trusted identity preserves private owner and tenant isolation."""
    subject = SubjectContext(
        tenant_id="tenant-a",
        user_id="user-a",
        organization_ids=("sales",),
        roles=(GovernedRole.MEMBER,),
    )
    private = ResourceScope(
        tenant_id="tenant-a",
        organization_id="sales",
        owner_type="user",
        owner_id="user-a",
        visibility=ResourceVisibility.PRIVATE,
    )
    foreign_owner = ResourceScope(
        tenant_id="tenant-a",
        organization_id="sales",
        owner_type="user",
        owner_id="user-b",
        visibility=ResourceVisibility.PRIVATE,
    )
    foreign_tenant = ResourceScope(
        tenant_id="tenant-b",
        organization_id="sales",
        owner_type="user",
        owner_id="user-a",
        visibility=ResourceVisibility.ORGANIZATION,
    )

    assert resource_scope_allows(subject, private) is True
    assert resource_scope_allows(subject, foreign_owner) is False
    assert resource_scope_allows(subject, foreign_tenant) is False


@pytest.mark.asyncio
async def test_production_identity_has_no_local_fallback() -> None:
    """Production identity resolution fails closed without verified claims."""
    with pytest.raises(GovernanceValidationError, match="verifier"):
        await verified_subject("unverified", verifier=None)

    class RejectingVerifier:
        """Represent signature, issuer, audience, or expiry validation failure."""

        async def verify(self, token: str) -> SubjectContext:
            """Reject the untrusted token instead of creating a local subject."""
            raise GovernanceValidationError("Token validation failed")

    with pytest.raises(GovernanceValidationError, match="Token validation failed"):
        await verified_subject("expired", verifier=RejectingVerifier())


@pytest.mark.asyncio
async def test_membership_is_server_resolved_and_cross_tenant_is_rejected(
    sessionmaker: async_sessionmaker,
) -> None:
    """Membership writes cannot manufacture roles across tenant boundaries."""
    async with sessionmaker() as session:
        service = EnterpriseGovernanceService(session)
        local, organization = await service.ensure_local_foundation()
        user = User(display_name="member")
        foreign = Tenant(id="foreign", name="Foreign", status="active")
        foreign_org = Organization(
            tenant_id="foreign", name="Foreign", type="enterprise", status="active"
        )
        session.add_all((user, foreign, foreign_org))
        await session.flush()
        await service.add_membership(
            tenant_id=local.id,
            organization_id=organization.id,
            user_id=user.id,
            role=GovernedRole.DEPARTMENT_ADMIN,
        )
        subject = await resolve_local_subject(session, user.id)

        assert subject.roles == (GovernedRole.DEPARTMENT_ADMIN,)
        with pytest.raises(GovernanceValidationError, match="cross tenants"):
            await service.add_membership(
                tenant_id="foreign",
                organization_id=foreign_org.id,
                user_id=user.id,
                role=GovernedRole.ENTERPRISE_ADMIN,
            )
        with pytest.raises(GovernanceValidationError, match="parent cannot cross"):
            await service.create_organization(
                tenant_id=local.id,
                name="Invalid child",
                parent_id=foreign_org.id,
            )


def test_policy_has_four_outcomes_deny_precedence_and_safe_constraints() -> None:
    """Policy evaluation is deterministic and never treats constraints as code."""
    subject = SubjectContext("tenant-a", "user-a", ("finance",), (GovernedRole.MANAGER,))
    resource = ResourceContext(
        "expense",
        "expense-1",
        ResourceScope(
            "tenant-a", "finance", "user", "user-a", ResourceVisibility.PRIVATE
        ),
    )
    statements = (
        PolicyStatement(
            effect=PolicyEffect.APPROVAL,
            action="expense.approve",
            scope_type="role",
            scope_id="manager",
            resource_type="expense",
            constraints={"amount": {"op": "gte", "value": 50_001}},
        ),
        PolicyStatement(
            effect=PolicyEffect.DENY,
            action="expense.approve",
            scope_type="enterprise",
            resource_type="expense",
        ),
    )

    decision = authorize(
        subject=subject,
        action="expense.approve",
        resource=resource,
        context={"amount": 80_000},
        statements=statements,
    )
    unsafe = authorize(
        subject=subject,
        action="expense.approve",
        resource=resource,
        context={"amount": 80_000},
        statements=(
            PolicyStatement(
                PolicyEffect.ALLOW,
                "expense.approve",
                constraints={"amount": {"op": "eval", "value": "__import__('os')"}},
            ),
        ),
    )

    assert decision == PolicyDecision(PolicyEffect.DENY, "explicit_deny")
    assert unsafe.effect is PolicyEffect.DENY
    assert {default_risk_effect(f"R{index}") for index in range(5)} == {
        PolicyEffect.ALLOW,
        PolicyEffect.DENY,
        PolicyEffect.CONFIRM,
        PolicyEffect.APPROVAL,
    }
    assert normalize_tool_risk("L3").value == "R3"


def test_delegation_can_only_narrow_existing_authority() -> None:
    """Delegation preserves tenant, Capability, depth, and constraint ceilings."""
    parent = CapabilityGrantView(
        tenant_id="tenant-a",
        source_organization_id="root",
        target_organization_id="finance",
        capability_key="finance.expense-review",
        version="2.1",
        can_delegate=True,
        max_delegate_depth=2,
        constraints={"amount_max": 50_000},
    )
    child = CapabilityGrantView(
        tenant_id="tenant-a",
        source_organization_id="finance",
        target_organization_id="finance-east",
        capability_key="finance.expense-review",
        version="2.1",
        can_delegate=True,
        max_delegate_depth=1,
        constraints={"amount_max": 20_000},
    )
    validate_delegation(parent, child)

    with pytest.raises(GovernanceValidationError, match="depth"):
        validate_delegation(
            parent,
            CapabilityGrantView(
                **{
                    **child.__dict__,
                    "max_delegate_depth": 2,
                }
            ),
        )
    with pytest.raises(GovernanceValidationError, match="constraints"):
        validate_delegation(
            parent,
            CapabilityGrantView(
                **{
                    **child.__dict__,
                    "constraints": {"amount_max": 80_000},
                }
            ),
        )


@pytest.mark.asyncio
async def test_profile_is_fresh_bounded_and_saved_before_agent_callback(
    sessionmaker: async_sessionmaker,
) -> None:
    """Application execution persists current grants before delegating to Agent runtime."""
    async with sessionmaker() as session:
        governance = EnterpriseGovernanceService(session)
        tenant, organization = await governance.ensure_local_foundation()
        user = User(display_name="analyst")
        session.add(user)
        await session.flush()
        await governance.add_membership(
            tenant_id=tenant.id,
            organization_id=organization.id,
            user_id=user.id,
            role=GovernedRole.MEMBER,
        )
        capability = CapabilityDefinition(
            tenant_id=tenant.id,
            key="office.spreadsheet",
            version="1.0",
            display_name="Spreadsheet",
            bindings_json='{"knowledge_scopes":["company.public"],"tools":["sheet.read"]}',
            status="active",
        )
        session.add(capability)
        await session.flush()
        grant = await governance.create_grant(
            tenant_id=tenant.id,
            source_organization_id=organization.id,
            target_organization_id=organization.id,
            capability_id=capability.id,
            version_constraint="1.0",
            bootstrap=True,
        )
        task = await TaskRepository(session).create_task(
            TaskCreate(
                user_id=user.id,
                platform="api",
                task_type="office",
                input_text="analyze",
            )
        )
        called: list[str] = []

        async def execute_agent(task_id: str) -> Task:
            """Assert the persisted immutable snapshot exists at the runtime boundary."""
            current = await session.get(Task, task_id)
            assert current is not None
            assert current.agent_profile_snapshot is not None
            called.append(current.agent_profile_snapshot)
            return current

        await TaskExecutionService(
            session,
            agent_task_executor=execute_agent,
        ).execute_task(task.id)
        assert called and "office.spreadsheet@1.0" in called[0]
        assert "sheet.read" in called[0]

        await governance.create_policy_rule(
            tenant_id=tenant.id,
            scope_type="enterprise",
            scope_id=None,
            action="capability.use:office.spreadsheet",
            resource_type="capability",
            effect=PolicyEffect.DENY,
        )
        denied_task = await TaskRepository(session).create_task(
            TaskCreate(
                user_id=user.id,
                platform="api",
                task_type="office",
                input_text="denied analysis",
            )
        )
        denied_profile = await GovernedAgentProfileResolver(session).resolve_for_task(
            denied_task
        )
        assert denied_profile.capabilities == ()

        grant.status = "revoked"
        tenant.authority_revision += 1
        second = await TaskRepository(session).create_task(
            TaskCreate(
                user_id=user.id,
                platform="api",
                task_type="office",
                input_text="analyze again",
            )
        )
        profile = await GovernedAgentProfileResolver(session).resolve_for_task(second)
        assert profile.capabilities == ()
        assert profile.subject.authority_revision == tenant.authority_revision


@pytest.mark.asyncio
async def test_expired_grant_is_ignored_and_policy_denial_stops_tool_handler(
    sessionmaker: async_sessionmaker,
) -> None:
    """Current authority is rechecked at resolution and exact tool execution."""
    async with sessionmaker() as session:
        governance = EnterpriseGovernanceService(session)
        tenant, organization = await governance.ensure_local_foundation()
        user = User(display_name="operator")
        session.add(user)
        await session.flush()
        capability = CapabilityDefinition(
            tenant_id=tenant.id,
            key="office.read",
            version="1.0",
            display_name="Read",
            bindings_json='{"tools":["demo.read"]}',
            status="active",
        )
        session.add(capability)
        await session.flush()
        await governance.create_grant(
            tenant_id=tenant.id,
            source_organization_id=organization.id,
            target_organization_id=organization.id,
            capability_id=capability.id,
            version_constraint="1.0",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            bootstrap=True,
        )
        task = await TaskRepository(session).create_task(
            TaskCreate(user_id=user.id, platform="api", task_type="office", input_text="read")
        )
        profile = await GovernedAgentProfileResolver(session).resolve_for_task(task)
        assert profile.capabilities == ()

        class DenyAuthorizer:
            """Return a current explicit deny for the exact invocation."""

            async def authorize(
                self, invocation: ToolInvocation, *, risk_level: str
            ) -> PolicyDecision:
                """Deny without trusting the earlier plan."""
                assert invocation.arguments == {"value": "safe"}
                assert risk_level == "R0"
                return PolicyDecision(PolicyEffect.DENY, "permission_revoked")

        calls: list[str] = []

        async def handler(invocation: ToolInvocation) -> dict[str, bool]:
            """Record any incorrect execution after policy denial."""
            calls.append(invocation.name)
            return {"ok": True}

        registry = ToolRegistry(session=session, policy_authorizer=DenyAuthorizer())
        registry.register(
            ToolSpec(
                name="demo.read",
                description="Read demo data",
                risk_level="L0",
                handler=handler,
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            )
        )
        with pytest.raises(ToolNotAllowedError, match="current policy"):
            await registry.execute(
                ToolInvocation(task.id, user.id, "demo.read", {"value": "safe"}),
                allowed_tools=("demo.read",),
                approval_required_tools=(),
            )
        assert calls == []


def test_migration_backfills_local_private_owner_scope() -> None:
    """The additive migration retains user ownership while adding tenant scope."""
    migration = (
        Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/202608100001_vnext_identity_policy_profile.py"
    ).read_text(encoding="utf-8")
    assert "owner_id = user_id" in migration
    assert "visibility = 'private'" in migration
    assert "LOCAL_TENANT_ID = \"local\"" in migration
