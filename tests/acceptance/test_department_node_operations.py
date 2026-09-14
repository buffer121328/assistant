from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from application.department_node_operations import DepartmentNodeOperationsService
from app.api.routers.enterprise import (
    acknowledge_node_operation,
    create_admin_node_enrollment,
    create_admin_node_operation,
    poll_node_operations,
    record_node_heartbeat,
    register_department_node,
)
from app.api.schemas.enterprise import (
    NodeEnrollmentCreateRequest,
    NodeHeartbeatRequest,
    NodeOperationAckRequest,
    NodeOperationCreateRequest,
    NodeRegistrationRequest,
)
from domain.models import (
    Base,
    CapabilityDefinition,
    CapabilityGrant,
    CapabilityVersion,
    DepartmentNode,
    DiagnosticPackage,
    GovernanceAudit,
    NodeConfigApplication,
    NodeRemoteOperation,
    Organization,
    Task,
    TaskContextSnapshot,
    Tenant,
    User,
)
from domain.policies.enterprise import GovernedRole, GovernanceValidationError, SubjectContext


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Provide an isolated schema for node-control acceptance boundaries."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as current:
        yield current
    await engine.dispose()


async def _seed(
    session: AsyncSession,
) -> tuple[SubjectContext, SubjectContext, SubjectContext, SubjectContext, Organization, Organization]:
    """Seed two departments and four server-resolved role contexts."""
    tenant = Tenant(id="tenant-a", name="Example", status="active")
    sales = Organization(id="org-sales", tenant_id=tenant.id, name="Sales", type="department", status="active")
    finance = Organization(id="org-finance", tenant_id=tenant.id, name="Finance", type="department", status="active")
    users = (
        User(id="admin", tenant_id=tenant.id, display_name="Admin"),
        User(id="sales-admin", tenant_id=tenant.id, display_name="Sales Admin"),
        User(id="auditor", tenant_id=tenant.id, display_name="Auditor"),
        User(id="employee", tenant_id=tenant.id, display_name="Employee"),
    )
    session.add_all((tenant, sales, finance, *users))
    await session.flush()
    return (
        SubjectContext(tenant.id, "admin", (sales.id, finance.id), (GovernedRole.ENTERPRISE_ADMIN,)),
        SubjectContext(tenant.id, "sales-admin", (sales.id,), (GovernedRole.DEPARTMENT_ADMIN,)),
        SubjectContext(tenant.id, "auditor", (), (GovernedRole.AUDITOR,)),
        SubjectContext(tenant.id, "employee", (sales.id,), (GovernedRole.MEMBER,)),
        sales,
        finance,
    )


async def _register(
    service: DepartmentNodeOperationsService,
    admin: SubjectContext,
    organization_id: str,
    *,
    name: str = "sales-node-1",
) -> tuple[DepartmentNode, str]:
    """Create and consume a one-time enrollment for test setup."""
    enrollment = await service.create_enrollment(admin, organization_id=organization_id, expires_in_minutes=60)
    registration = await service.register_node(
        enrollment_token=str(enrollment["enrollment_token"]), name=name, agent_version="1.0.0"
    )
    node = await service.authenticate_node(str(registration["node_token"]))
    return node, str(registration["node_token"])


@pytest.mark.asyncio
async def test_node_enrollment_is_single_use_heartbeat_is_scoped_and_revocation_is_immediate(
    session: AsyncSession,
) -> None:
    admin, sales_admin, _, employee, sales, _ = await _seed(session)
    service = DepartmentNodeOperationsService(session)
    enrollment = await service.create_enrollment(admin, organization_id=sales.id, expires_in_minutes=60)
    registration = await service.register_node(
        enrollment_token=str(enrollment["enrollment_token"]), name="sales-node", agent_version="1.0.0"
    )
    with pytest.raises(GovernanceValidationError, match="enrollment is invalid"):
        await service.register_node(
            enrollment_token=str(enrollment["enrollment_token"]), name="replay", agent_version="1.0.0"
        )
    node = await service.authenticate_node(str(registration["node_token"]))
    heartbeat = await service.heartbeat(
        node, agent_version="1.0.1", applied_config_revision=0,
        accepts_new_work=True, health_status="healthy", health_summary="ready",
    )
    assert heartbeat["connection_state"] == "online"
    assert [item["id"] for item in await service.list_nodes(sales_admin)] == [node.id]
    with pytest.raises(GovernanceValidationError, match="Administrative role"):
        await service.list_nodes(employee)
    await service.revoke_node(admin, node_id=node.id)
    with pytest.raises(GovernanceValidationError, match="credential is invalid"):
        await service.authenticate_node(str(registration["node_token"]))
    assert await session.scalar(select(func.count()).select_from(GovernanceAudit)) == 3


@pytest.mark.asyncio
async def test_node_configuration_is_deterministic_authorized_and_retains_last_known_good(
    session: AsyncSession,
) -> None:
    admin, _, _, _, sales, finance = await _seed(session)
    service = DepartmentNodeOperationsService(session)
    node, _ = await _register(service, admin, sales.id)
    definition = CapabilityDefinition(
        id="cap-sales", tenant_id=admin.tenant_id, key="crm-read", version="1",
        display_name="CRM Read", summary="Read sales records", status="active",
    )
    version = CapabilityVersion(
        id="cap-version-sales", tenant_id=admin.tenant_id, capability_id=definition.id,
        version="1.2.0", composition_json='{"tools":["crm.read"],"model_policy":"balanced"}',
        published_by=admin.user_id,
    )
    sales_grant = CapabilityGrant(
        tenant_id=admin.tenant_id, source_organization_id=sales.id,
        target_organization_id=sales.id, capability_id=definition.id,
        version_constraint=version.version, constraints_json="{}", status="active",
    )
    finance_grant = CapabilityGrant(
        tenant_id=admin.tenant_id, source_organization_id=finance.id,
        target_organization_id=finance.id, capability_id=definition.id,
        version_constraint=version.version, constraints_json="{}", status="active",
    )
    session.add_all((definition, version, sales_grant, finance_grant))
    await session.flush()

    first = await service.desired_configuration(node)
    replay = await service.desired_configuration(node)
    assert first == replay
    capabilities = first["configuration"]["capabilities"]  # type: ignore[index]
    assert len(capabilities) == 1
    assert capabilities[0]["capability_version_id"] == version.id
    assert capabilities[0]["composition"] == {
        "tools": ["crm.read"], "model_policy": "balanced"
    }

    first_revision = cast(int, first["revision"])
    applied = await service.acknowledge_configuration(
        node, revision=first_revision, status="applied", error_code=None, error_summary=""
    )
    assert applied["applied_config_revision"] == first["revision"]
    definition.status = "disabled"
    second = await service.desired_configuration(node)
    assert second["revision"] != first["revision"]
    second_revision = cast(int, second["revision"])
    failed = await service.acknowledge_configuration(
        node, revision=second_revision, status="failed",
        error_code="invalid_config", error_summary="validation failed",
    )
    assert failed["applied_config_revision"] == first["revision"]
    assert await session.scalar(select(func.count()).select_from(NodeConfigApplication)) == 2


@pytest.mark.asyncio
async def test_diagnostics_are_redacted_filterable_and_department_scoped(session: AsyncSession) -> None:
    admin, sales_admin, auditor, _, sales, finance = await _seed(session)
    service = DepartmentNodeOperationsService(session)
    sales_node, _ = await _register(service, admin, sales.id)
    now = datetime.now(UTC)
    sales_task = Task(
        id="task-sales", tenant_id=admin.tenant_id, organization_id=sales.id,
        node_id=sales_node.id, user_id="employee", platform="desktop", task_type="plan",
        input_text="private input", status="failed",
        error_message="token=secret at /private/customer/file.csv", created_at=now,
    )
    finance_task = Task(
        id="task-finance", tenant_id=admin.tenant_id, organization_id=finance.id,
        user_id="employee", platform="desktop", task_type="plan", input_text="finance private",
        status="failed", error_message="timeout while calling provider", created_at=now,
    )
    snapshot = TaskContextSnapshot(
        task_id=sales_task.id, user_id="employee", tenant_id=admin.tenant_id,
        organization_id=sales.id, owner_type="user", owner_id="employee", visibility="private",
        capability_snapshot_json='["crm-read@1.2.0"]',
    )
    session.add_all((sales_task, finance_task, snapshot))
    await session.flush()

    records = await service.query_diagnostics(
        sales_admin, start_at=now - timedelta(hours=1), end_at=now + timedelta(hours=1),
        organization_id=sales.id, node_id=sales_node.id, status="failed",
        capability_version="crm-read@1.2.0",
    )
    assert [item["task_id"] for item in records] == [sales_task.id]
    assert records[0]["error_summary"] == "[REDACTED]"
    assert "input_text" not in records[0]
    with pytest.raises(GovernanceValidationError, match="Organization is unavailable"):
        await service.query_diagnostics(
            sales_admin, start_at=now - timedelta(hours=1), end_at=now,
            organization_id=finance.id,
        )
    assert len(await service.query_diagnostics(
        auditor, start_at=now - timedelta(hours=1), end_at=now + timedelta(hours=1)
    )) == 2
    tenant_package = await service.create_diagnostic_package(
        admin, start_at=now - timedelta(hours=1), end_at=now + timedelta(hours=1),
        expires_in_minutes=60,
    )
    with pytest.raises(GovernanceValidationError, match="package is unavailable"):
        await service.download_diagnostic_package(sales_admin, str(tenant_package["id"]))


@pytest.mark.asyncio
async def test_diagnostic_package_expires_and_generation_and_download_are_audited(
    session: AsyncSession,
) -> None:
    admin, _, _, _, sales, _ = await _seed(session)
    current = [datetime(2026, 8, 23, 10, 0, tzinfo=UTC)]
    service = DepartmentNodeOperationsService(session, clock=lambda: current[0])
    task = Task(
        id="task-package", tenant_id=admin.tenant_id, organization_id=sales.id,
        user_id="employee", platform="desktop", task_type="plan", input_text="hidden",
        status="failed", error_message="safe failure", created_at=current[0],
    )
    session.add(task)
    await session.flush()
    package = await service.create_diagnostic_package(
        admin, start_at=current[0] - timedelta(minutes=5), end_at=current[0] + timedelta(minutes=5),
        expires_in_minutes=5, organization_id=sales.id,
    )
    download = json.loads(await service.download_diagnostic_package(admin, str(package["id"])))
    assert download["checksum"] == package["checksum"]
    assert download["manifest"]["records"][0]["task_id"] == task.id
    assert "input_text" not in download["manifest"]["records"][0]
    current[0] += timedelta(minutes=6)
    with pytest.raises(GovernanceValidationError, match="package is unavailable"):
        await service.download_diagnostic_package(admin, str(package["id"]))
    stored = await session.get(DiagnosticPackage, str(package["id"]))
    assert stored is not None and stored.downloaded_at is not None
    audits = tuple(await session.scalars(select(GovernanceAudit).where(GovernanceAudit.resource_id == package["id"])))
    assert {item.result_status for item in audits} == {
        "diagnostic_package_created", "diagnostic_package_downloaded"
    }


@pytest.mark.asyncio
async def test_remote_operations_are_whitelisted_scoped_idempotent_and_acknowledged(
    session: AsyncSession,
) -> None:
    admin, sales_admin, _, _, sales, finance = await _seed(session)
    service = DepartmentNodeOperationsService(session)
    node, _ = await _register(service, admin, sales.id)
    running = Task(
        id="task-running", tenant_id=admin.tenant_id, organization_id=sales.id,
        node_id=node.id, user_id="employee", platform="desktop", task_type="plan",
        input_text="hidden", status="running",
    )
    foreign = Task(
        id="task-foreign", tenant_id=admin.tenant_id, organization_id=finance.id,
        user_id="employee", platform="desktop", task_type="plan", input_text="hidden",
        status="running",
    )
    session.add_all((running, foreign))
    await session.flush()
    created = await service.create_operation(
        sales_admin, node_id=node.id, operation_type="stop_task",
        target_task_id=running.id, idempotency_key="operation-1", expires_in_minutes=10,
    )
    replay = await service.create_operation(
        sales_admin, node_id=node.id, operation_type="stop_task",
        target_task_id=running.id, idempotency_key="operation-1", expires_in_minutes=10,
    )
    assert replay["id"] == created["id"]
    with pytest.raises(GovernanceValidationError, match="Task is unavailable"):
        await service.create_operation(
            sales_admin, node_id=node.id, operation_type="stop_task",
            target_task_id=foreign.id, idempotency_key="operation-foreign", expires_in_minutes=10,
        )
    with pytest.raises(GovernanceValidationError, match="enterprise authority"):
        await service.create_operation(
            sales_admin, node_id=node.id, operation_type="restart_agent",
            target_task_id=None, idempotency_key="operation-restart", expires_in_minutes=10,
        )
    delivered = await service.poll_operations(node)
    assert [item["id"] for item in delivered] == [created["id"]]
    completed = await service.acknowledge_operation(
        node, operation_id=str(created["id"]), status="succeeded",
        result_code="task_stop_requested", result_summary="accepted",
    )
    replay_ack = await service.acknowledge_operation(
        node, operation_id=str(created["id"]), status="succeeded",
        result_code="ignored", result_summary="ignored",
    )
    assert replay_ack == completed
    await session.refresh(running)
    assert running.status == "cancelled"
    assert await session.scalar(select(func.count()).select_from(NodeRemoteOperation)) == 1


@pytest.mark.asyncio
async def test_expired_enrollment_operation_and_foreign_tenant_access_fail_closed(
    session: AsyncSession,
) -> None:
    admin, _, _, _, sales, _ = await _seed(session)
    current = [datetime(2026, 8, 23, 10, 0, tzinfo=UTC)]
    service = DepartmentNodeOperationsService(session, clock=lambda: current[0])
    node, _ = await _register(service, admin, sales.id, name="expiry-node")
    operation = await service.create_operation(
        admin, node_id=node.id, operation_type="refresh_config", target_task_id=None,
        idempotency_key="expiry-operation", expires_in_minutes=1,
    )
    enrollment = await service.create_enrollment(admin, organization_id=sales.id, expires_in_minutes=5)
    revoked = await service.create_enrollment(admin, organization_id=sales.id, expires_in_minutes=5)
    revoked_result = await service.revoke_enrollment(admin, enrollment_id=str(revoked["id"]))
    assert revoked_result["status"] == "revoked"
    with pytest.raises(GovernanceValidationError, match="enrollment is invalid"):
        await service.register_node(
            enrollment_token=str(revoked["enrollment_token"]), name="revoked", agent_version="1"
        )
    current[0] += timedelta(minutes=6)
    assert await service.poll_operations(node) == ()
    expired_operation = await session.get(NodeRemoteOperation, str(operation["id"]))
    assert expired_operation is not None and expired_operation.status == "expired"
    with pytest.raises(GovernanceValidationError, match="enrollment is invalid"):
        await service.register_node(
            enrollment_token=str(enrollment["enrollment_token"]), name="late", agent_version="1"
        )
    foreign = SubjectContext("tenant-b", "foreign-admin", (), (GovernedRole.ENTERPRISE_ADMIN,))
    assert await service.list_nodes(foreign) == ()


@pytest.mark.asyncio
async def test_authenticated_routes_expose_node_registration_heartbeat_and_operation_contract(
    session: AsyncSession,
) -> None:
    """Exercise the administrator and node-facing protocol adapters without bypassing services."""
    admin, _, _, _, sales, _ = await _seed(session)
    enrollment = await create_admin_node_enrollment(
        payload=NodeEnrollmentCreateRequest(organization_id=sales.id, expires_in_minutes=60),
        subject=admin,
        session=session,
    )
    registration = await register_department_node(
        payload=NodeRegistrationRequest(
            enrollment_token=enrollment.enrollment_token,
            name="route-node",
            agent_version="1.0.0",
        ),
        session=session,
    )
    node = await DepartmentNodeOperationsService(session).authenticate_node(registration.node_token)
    heartbeat = await record_node_heartbeat(
        payload=NodeHeartbeatRequest(
            agent_version="1.0.1",
            applied_config_revision=0,
            accepts_new_work=True,
            health_status="healthy",
            health_summary="ready",
        ),
        node=node,
        session=session,
    )
    created = await create_admin_node_operation(
        node_id=node.id,
        payload=NodeOperationCreateRequest(
            operation_type="pause_new_work", expires_in_minutes=10
        ),
        subject=admin,
        session=session,
        idempotency_key="route-operation",
    )
    delivered = await poll_node_operations(node=node, session=session)
    completed = await acknowledge_node_operation(
        operation_id=created.items[0].id,
        payload=NodeOperationAckRequest(
            status="succeeded", result_code="paused", result_summary="accepted"
        ),
        node=node,
        session=session,
    )

    assert heartbeat.items[0].connection_state == "online"
    assert delivered.items[0].operation_type == "pause_new_work"
    assert completed.items[0].status == "succeeded"
    assert node.accepts_new_work is False


def test_local_acceptance_guide_tracks_reachable_safe_protocol() -> None:
    """Keep the hand-run acceptance path aligned with shipped routes and limits."""
    root = Path(__file__).resolve().parents[2]
    guide_path = root / "reference/department-node-operations-acceptance.md"
    guide = guide_path.read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    for contract in (
        "工作区 → 企业管理台",
        "POST /api/nodes/register",
        "/api/nodes/heartbeat",
        "/api/nodes/configuration",
        "/api/nodes/configuration/ack",
        "/api/nodes/operations",
        "X-Assistant-Node",
        "last-known-good",
        "不提供 Shell、远程桌面、文件浏览、凭证读取或任意命令执行",
    ):
        assert contract in guide
    assert "department-node-operations-acceptance.md" in readme
    assert "unset ASSISTANT_ACCEPT_NODE_TOKEN" in guide
