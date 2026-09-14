from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from application.capability_requests import CapabilityRequestService
from app.api.routers.enterprise import (
    create_my_capability_request,
    list_admin_capability_requests,
    list_my_capability_requests,
    update_admin_capability_request_status,
)
from app.api.schemas.enterprise import (
    CapabilityRequestCreateRequest,
    CapabilityRequestStatusUpdateRequest,
)
from domain.models import (
    Base,
    CapabilityRequest,
    GovernanceAudit,
    Organization,
    OrganizationMembership,
    Task,
    Tenant,
    User,
)
from domain.policies.enterprise import GovernedRole, GovernanceValidationError, SubjectContext


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as current:
        yield current
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[SubjectContext, SubjectContext, SubjectContext, Task]:
    tenant = Tenant(id="tenant-a", name="Example", status="active")
    organization = Organization(
        id="org-a", tenant_id=tenant.id, name="Finance", type="department", status="active"
    )
    employee = User(id="user-a", tenant_id=tenant.id, display_name="Employee A")
    other = User(id="user-b", tenant_id=tenant.id, display_name="Employee B")
    publisher = User(id="publisher-a", tenant_id=tenant.id, display_name="Publisher")
    task = Task(
        id="task-b",
        tenant_id=tenant.id,
        user_id=other.id,
        platform="desktop",
        task_type="plan",
        input_text="private task",
        status="pending",
    )
    session.add_all((tenant, organization, employee, other, publisher, task))
    await session.flush()
    return (
        SubjectContext(tenant.id, employee.id, (organization.id,), (GovernedRole.MEMBER,)),
        SubjectContext(tenant.id, other.id, (organization.id,), (GovernedRole.MEMBER,)),
        SubjectContext(
            tenant.id, publisher.id, (organization.id,), (GovernedRole.CAPABILITY_PUBLISHER,)
        ),
        task,
    )


@pytest.mark.asyncio
async def test_employee_request_is_idempotent_owner_scoped_and_audited(session: AsyncSession) -> None:
    employee, other, publisher, _ = await _seed(session)
    service = CapabilityRequestService(session)

    created = await service.create(
        employee,
        title="Need contract review",
        description="Help review contract clauses under company policy.",
        related_task_id=None,
        idempotency_key="request-1",
    )
    replay = await service.create(
        employee,
        title="ignored on replay",
        description="ignored on replay",
        related_task_id=None,
        idempotency_key="request-1",
    )
    await session.commit()

    assert replay["id"] == created["id"]
    assert await session.scalar(select(func.count()).select_from(CapabilityRequest)) == 1
    assert [item["id"] for item in await service.list_personal(employee)] == [created["id"]]
    assert await service.list_personal(other) == ()
    queue = await service.list_admin(publisher)
    assert queue[0]["requester_display_name"] == "Employee A"
    assert queue[0]["organization_name"] == "Finance"
    audit = await session.scalar(
        select(GovernanceAudit).where(GovernanceAudit.resource_id == created["id"])
    )
    assert audit is not None
    assert audit.result_status == "request_created"


@pytest.mark.asyncio
async def test_request_rejects_foreign_task_and_unauthorized_queue_access(session: AsyncSession) -> None:
    employee, _, _, foreign_task = await _seed(session)
    service = CapabilityRequestService(session)

    with pytest.raises(GovernanceValidationError, match="task is unavailable"):
        await service.create(
            employee,
            title="Need task help",
            description="The task should not be visible.",
            related_task_id=foreign_task.id,
            idempotency_key="request-foreign-task",
        )
    with pytest.raises(GovernanceValidationError, match="Administrative role"):
        await service.list_admin(employee)
    assert await session.scalar(select(func.count()).select_from(CapabilityRequest)) == 0


@pytest.mark.asyncio
async def test_it_transition_updates_employee_view_and_closed_is_terminal(session: AsyncSession) -> None:
    employee, _, publisher, _ = await _seed(session)
    service = CapabilityRequestService(session)
    created = await service.create(
        employee,
        title="Need reporting capability",
        description="Create a department reporting workflow.",
        related_task_id=None,
        idempotency_key="request-transition",
    )

    triaged = await service.update_status(
        publisher, request_id=str(created["id"]), status="communicating"
    )
    assert triaged["status"] == "communicating"
    personal = await service.list_personal(employee)
    assert personal[0]["status"] == "communicating"
    await service.update_status(publisher, request_id=str(created["id"]), status="closed")
    with pytest.raises(GovernanceValidationError, match="transition is invalid"):
        await service.update_status(
            publisher, request_id=str(created["id"]), status="configuring"
        )
    audits = tuple(await session.scalars(
        select(GovernanceAudit).where(GovernanceAudit.resource_id == created["id"])
    ))
    assert [audit.result_status for audit in audits].count("status_changed") == 2


@pytest.mark.asyncio
async def test_only_ai_department_administrator_can_publish_a_capability_request(
    session: AsyncSession,
) -> None:
    """Draft triage may be delegated, but publication requires active AI leadership."""
    employee, _, publisher, _ = await _seed(session)
    service = CapabilityRequestService(session)
    created = await service.create(
        employee,
        title="Need governed MCP search",
        description="Design a bounded MCP search capability.",
        related_task_id=None,
        idempotency_key="request-ai-approval",
    )
    request_id = str(created["id"])
    for next_status in ("communicating", "configuring", "testing", "pending_approval"):
        await service.update_status(publisher, request_id=request_id, status=next_status)

    with pytest.raises(GovernanceValidationError, match="AI department administrator"):
        await service.update_status(publisher, request_id=request_id, status="published")

    ai_department = Organization(
        id="org-ai",
        tenant_id=employee.tenant_id,
        name="AI部",
        type="department",
        status="active",
    )
    ai_admin = User(
        id="ai-admin",
        tenant_id=employee.tenant_id,
        display_name="AI负责人",
    )
    session.add_all((ai_department, ai_admin))
    await session.flush()
    session.add(
        OrganizationMembership(
            tenant_id=employee.tenant_id,
            organization_id=ai_department.id,
            user_id=ai_admin.id,
            role=GovernedRole.DEPARTMENT_ADMIN.value,
            status="active",
        )
    )
    await session.flush()
    approved = await service.update_status(
        SubjectContext(
            employee.tenant_id,
            ai_admin.id,
            (ai_department.id,),
            (GovernedRole.DEPARTMENT_ADMIN,),
        ),
        request_id=request_id,
        status="published",
    )
    assert approved["status"] == "published"


@pytest.mark.asyncio
async def test_admin_cannot_read_or_update_another_tenant_request(session: AsyncSession) -> None:
    employee, _, _, _ = await _seed(session)
    service = CapabilityRequestService(session)
    created = await service.create(
        employee,
        title="Need finance export",
        description="Provide an approved finance export.",
        related_task_id=None,
        idempotency_key="request-tenant",
    )
    foreign_admin = SubjectContext(
        "tenant-b", "publisher-b", (), (GovernedRole.CAPABILITY_PUBLISHER,)
    )
    assert await service.list_admin(foreign_admin) == ()
    with pytest.raises(GovernanceValidationError, match="unavailable"):
        await service.update_status(
            foreign_admin, request_id=str(created["id"]), status="communicating"
        )


@pytest.mark.asyncio
async def test_authenticated_routes_expose_employee_and_it_request_contract(session: AsyncSession) -> None:
    employee, _, publisher, _ = await _seed(session)
    created = await create_my_capability_request(
        payload=CapabilityRequestCreateRequest(
            title="Need approved export",
            description="Export a governed department report.",
        ),
        subject=employee,
        session=session,
        idempotency_key="route-create",
    )
    personal = await list_my_capability_requests(subject=employee, session=session)
    queue = await list_admin_capability_requests(
        subject=publisher, session=session, status=None
    )
    updated = await update_admin_capability_request_status(
        request_id=created.id,
        payload=CapabilityRequestStatusUpdateRequest(status="communicating"),
        subject=publisher,
        session=session,
        idempotency_key="route-status",
    )

    assert [item.id for item in personal.items] == [created.id]
    assert queue.items[0].requester_display_name == "Employee A"
    assert updated.status == "communicating"
