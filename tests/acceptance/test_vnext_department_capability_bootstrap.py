from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.capabilities import CapabilityRegistry, build_default_registry
from app.api.routers.enterprise import (
    bootstrap_admin_departments,
    provision_admin_member,
    record_admin_marketplace_skill_candidate,
)
from app.api.routers.auth import initialize
from app.api.schemas.auth import LocalInitializeRequest
from app.api.schemas.enterprise import (
    AdminMarketplaceSkillCandidateRequest,
    AdminMemberProvisionRequest,
)
from app.support.errors import AppError
from application.department_bootstrap import DepartmentBootstrapService
from application.enterprise_runtime import EnterpriseAdminReadService, EnterpriseAdminWriteService
from application.enterprise_governance import (
    EnterpriseGovernanceService,
    resolve_local_subject,
)
from application.marketplace_skill_candidates import MarketplaceSkillCandidateService
from domain.models import (
    Base,
    CapabilityGrant,
    GovernanceAudit,
    LocalCredential,
    Organization,
    OrganizationMembership,
    SkillAuditLog,
    SkillDefinition,
    SkillDraft,
    User,
)
from domain.policies.enterprise import GovernedRole, GovernanceValidationError


ROOT = Path(__file__).parents[2]
FINANCE_DEMO_CAPABILITY = "capability.finance.office-productivity"
MARKETING_DEMO_CAPABILITY = "capability.market.content-productivity"
AI_DEMO_CAPABILITY = "capability.ai.improvement-operations"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Provide isolated persistence for the department baseline scenarios."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as current:
        yield current
    await engine.dispose()


async def _enterprise_admin(session: AsyncSession):
    """Create the governed local tenant and one enterprise administrator."""
    governance = EnterpriseGovernanceService(session)
    tenant, root = await governance.ensure_local_foundation()
    user = User(tenant_id=tenant.id, display_name="企业管理员")
    session.add(user)
    await session.flush()
    await governance.add_membership(
        tenant_id=tenant.id,
        organization_id=root.id,
        user_id=user.id,
        role=GovernedRole.ENTERPRISE_ADMIN,
    )
    return await resolve_local_subject(session, user.id)


@pytest.mark.asyncio
async def test_ai_department_approval_is_required_for_connector_activation(
    session: AsyncSession,
) -> None:
    """An enterprise role alone cannot activate a Connector outside AI leadership."""
    subject = await _enterprise_admin(session)
    writes = EnterpriseAdminWriteService(session)

    with pytest.raises(GovernanceValidationError, match="AI department administrator"):
        await writes.create_connector(
            subject,
            key="issue-radar",
            connector_type="MCP",
            display_name="问题雷达",
        )

    await DepartmentBootstrapService(
        session,
        capability_registry=build_default_registry(
            ROOT / "backend" / "resources" / "skillpacks"
        ),
    ).bootstrap(subject)
    refreshed = await resolve_local_subject(session, subject.user_id)
    created = await writes.create_connector(
        refreshed,
        key="issue-radar",
        connector_type="MCP",
        display_name="问题雷达",
    )
    assert created["status"] == "active"


@pytest.mark.asyncio
async def test_first_administrator_initialization_rolls_back_when_demo_catalog_is_unavailable(
    session: AsyncSession,
) -> None:
    """A missing required demo Capability cannot leave partial identity or org state."""
    app = FastAPI()
    app.state.capability_registry = CapabilityRegistry()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/initialize",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
    )

    with pytest.raises(AppError) as failure:
        await initialize(
            request=request,
            payload=LocalInitializeRequest(
                display_name="企业管理员",
                login_name="admin.local",
                password="correct-horse-battery-staple",
            ),
            session=session,
        )

    assert failure.value.code == "initialization_unavailable"
    assert await session.scalar(select(LocalCredential)) is None
    assert tuple(await session.scalars(select(Organization))) == ()


@pytest.mark.asyncio
async def test_marketplace_candidate_is_disabled_undistributed_and_audited(
    session: AsyncSession,
) -> None:
    """Given a capability publisher, cataloging a candidate never installs or distributes it."""
    subject = await _enterprise_admin(session)

    candidate = await MarketplaceSkillCandidateService(session).record(
        subject,
        key="finance-pdf-review",
        display_name="财务文档与 PDF 审阅",
        summary="候选能力；必须经过审查和发布后才能使用。",
    )
    await session.commit()

    skill = await session.get(SkillDefinition, candidate["id"])
    draft = await session.scalar(
        select(SkillDraft).where(SkillDraft.skill_id == candidate["id"])
    )
    audit = await session.scalar(
        select(SkillAuditLog).where(
            SkillAuditLog.actor_user_id == subject.user_id,
            SkillAuditLog.skill_name == "finance-pdf-review",
            SkillAuditLog.action == "candidate_record",
        )
    )
    assert skill is not None
    assert draft is not None
    assert audit is not None
    assert candidate["status"] == skill.status == "candidate_disabled"
    assert candidate["stable_version"] is None
    assert candidate["distributed"] is False
    assert candidate["executable"] is False
    assert audit.status == "disabled"
    assert json.loads(draft.content_json) == {
        "acquired": False,
        "catalog_source": "marketplace",
        "enabled": False,
        "summary": "候选能力；必须经过审查和发布后才能使用。",
    }
    assert not tuple(
        await session.scalars(
            select(CapabilityGrant).where(
                CapabilityGrant.tenant_id == subject.tenant_id
            )
        )
    )


@pytest.mark.asyncio
async def test_admin_bootstrap_and_candidate_routes_write_bounded_governance_audits(
    session: AsyncSession,
) -> None:
    """Given an enterprise admin, management writes are idempotent and audit recorded."""
    subject = await _enterprise_admin(session)
    app = FastAPI()
    app.state.capability_registry = build_default_registry(
        ROOT / "backend" / "resources" / "skillpacks"
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/admin/departments/bootstrap",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
    )

    bootstrap = await bootstrap_admin_departments(
        request=request,
        subject=subject,
        session=session,
        idempotency_key="bootstrap-finance-technology",
    )
    retried_bootstrap = await bootstrap_admin_departments(
        request=request,
        subject=subject,
        session=session,
        idempotency_key="bootstrap-finance-technology",
    )
    candidate = await record_admin_marketplace_skill_candidate(
        payload=AdminMarketplaceSkillCandidateRequest(
            key="repository-diagnostics",
            display_name="仓库诊断",
            summary="候选能力；尚未安装、启用或分发。",
        ),
        subject=subject,
        session=session,
        idempotency_key="candidate-repository-diagnostics",
    )

    assert bootstrap.items == retried_bootstrap.items
    assert bootstrap.items[0]["it_department_created"] is False
    assert candidate.items[0]["status"] == "candidate_disabled"
    audit_rows = tuple(
        await session.scalars(
            select(GovernanceAudit).where(
                GovernanceAudit.subject_id == subject.user_id,
                GovernanceAudit.resource_type == "admin_mutation",
            )
        )
    )
    assert {row.tool_key for row in audit_rows} == {
        "departments.bootstrap",
        "skill.candidate.record",
    }
    assert all(row.policy_decision == "ALLOW" for row in audit_rows)




@pytest.mark.asyncio
async def test_department_bootstrap_and_candidate_catalog_reject_non_administrators(
    session: AsyncSession,
) -> None:
    """Given a regular employee, governed bootstrap and candidate writes fail closed."""
    admin = await _enterprise_admin(session)
    user = User(tenant_id=admin.tenant_id, display_name="普通员工")
    session.add(user)
    await session.flush()
    root_membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == admin.user_id
        )
    )
    assert root_membership is not None
    governance = EnterpriseGovernanceService(session)
    await governance.add_membership(
        tenant_id=admin.tenant_id,
        organization_id=root_membership.organization_id,
        user_id=user.id,
        role=GovernedRole.MEMBER,
    )
    subject = await resolve_local_subject(session, user.id)
    registry = build_default_registry(ROOT / "backend" / "resources" / "skillpacks")

    with pytest.raises(GovernanceValidationError):
        await DepartmentBootstrapService(session, capability_registry=registry).bootstrap(
            subject
        )
    with pytest.raises(GovernanceValidationError):
        await MarketplaceSkillCandidateService(session).record(
            subject,
            key="repository-diagnostics",
            display_name="仓库诊断",
            summary="候选能力。",
        )

@pytest.mark.asyncio
async def test_department_grant_reads_are_role_scoped(session: AsyncSession) -> None:
    """Given active grants, only enterprise and in-scope department admins may read them."""
    admin = await _enterprise_admin(session)
    registry = build_default_registry(ROOT / "backend" / "resources" / "skillpacks")
    await DepartmentBootstrapService(session, capability_registry=registry).bootstrap(admin)
    finance = await session.scalar(
        select(Organization).where(
            Organization.tenant_id == admin.tenant_id, Organization.name == "财务部"
        )
    )
    technology = await session.scalar(
        select(Organization).where(
            Organization.tenant_id == admin.tenant_id, Organization.name == "市场部"
        )
    )
    assert finance is not None and technology is not None
    reads = EnterpriseAdminReadService(session)

    grants = await reads.list_organization_capabilities(admin, organization_id=finance.id)
    grant_keys = {item["key"] for item in grants}
    assert FINANCE_DEMO_CAPABILITY in grant_keys
    assert all(item["version"] and item["distributed_at"] for item in grants)

    catalog = await reads.list_capabilities(admin)
    technology_version = next(
        item for item in catalog if item["key"] == MARKETING_DEMO_CAPABILITY
    )
    await EnterpriseAdminWriteService(session).distribute_capability(
        admin,
        capability_version_id=str(technology_version["capability_version_id"]),
        organization_id=finance.id,
    )
    grants_after = await reads.list_organization_capabilities(admin, organization_id=finance.id)
    assert MARKETING_DEMO_CAPABILITY in {item["key"] for item in grants_after}

    catalog_rows = await reads.list_capabilities(admin)
    marketing_row = next(item for item in catalog_rows if item["key"] == MARKETING_DEMO_CAPABILITY)
    assert "财务部" in marketing_row["departments"]

    provisioned = await provision_admin_member(
        payload=AdminMemberProvisionRequest(
            display_name="财务负责人",
            login_name="finance.grant.lead",
            password="correct-horse-battery-staple",
            organization_id=finance.id,
            role="member",
        ),
        subject=admin,
        session=session,
        idempotency_key="provision-finance-grant-lead",
    )
    lead_id = str(provisioned.items[0]["user_id"])
    await EnterpriseAdminWriteService(session).assign_member_role(
        admin,
        user_id=lead_id,
        organization_id=finance.id,
        role="department_admin",
    )
    lead = await resolve_local_subject(session, lead_id)
    scoped = await reads.list_organization_capabilities(lead, organization_id=finance.id)
    assert MARKETING_DEMO_CAPABILITY in {item["key"] for item in scoped}

    lead_catalog = await reads.list_capabilities(lead)
    lead_marketing_row = next(item for item in lead_catalog if item["key"] == MARKETING_DEMO_CAPABILITY)
    assert lead_marketing_row["departments"] == ["财务部"]
    with pytest.raises(GovernanceValidationError):
        await reads.list_organization_capabilities(lead, organization_id=technology.id)

    with pytest.raises(GovernanceValidationError):
        await reads.list_organization_capabilities(admin, organization_id="missing-org")

    await EnterpriseAdminWriteService(session).assign_member_role(
        admin,
        user_id=lead_id,
        organization_id=finance.id,
        role="member",
    )
    employee = await resolve_local_subject(session, lead_id)
    with pytest.raises(GovernanceValidationError):
        await reads.list_organization_capabilities(employee, organization_id=finance.id)
