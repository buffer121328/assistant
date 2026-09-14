from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.enterprise import (
    AdminCapabilityDistributionRequest,
    AdminConnectorCreateRequest,
    AdminConnectorInstanceCreateRequest,
    AdminConnectorToolReviewRequest,
    AdminListResponse,
    AdminMemberProvisionRequest,
    AdminMemberRoleAssignmentRequest,
    AdminMarketplaceSkillCandidateRequest,
    AdminOrganizationCreateRequest,
    CapabilityRequestCreateRequest,
    CapabilityRequestListResponse,
    CapabilityRequestResponse,
    CapabilityRequestStatusUpdateRequest,
    EffectiveCapabilityResponse,
    AuthorizedSkillResponse,
    MeResponse,
    MyCapabilitiesResponse,
    DiagnosticPackageCreateRequest,
    DiagnosticPackageListResponse,
    DiagnosticPackageResponse,
    DiagnosticRecordListResponse,
    DiagnosticRecordResponse,
    DepartmentNodeListResponse,
    DepartmentNodeResponse,
    NodeConfigAckRequest,
    NodeConfigAckResponse,
    NodeConfigurationResponse,
    NodeEnrollmentCreateRequest,
    NodeEnrollmentResponse,
    NodeEnrollmentStateResponse,
    NodeHeartbeatRequest,
    NodeOperationAckRequest,
    NodeOperationCreateRequest,
    NodeOperationListResponse,
    NodeOperationResponse,
    NodeRegistrationRequest,
    NodeRegistrationResponse,
)
from application.department_bootstrap import DepartmentBootstrapService
from application.capability_requests import CapabilityRequestService
from application.department_node_operations import DepartmentNodeOperationsService
from application.enterprise_governance import GovernedAgentProfileResolver
from application.local_auth import LocalAuthError
from application.marketplace_skill_candidates import MarketplaceSkillCandidateService
from application.enterprise_runtime import EnterpriseAdminReadService, EnterpriseAdminWriteService
from domain.models import CapabilityDefinition, DepartmentNode, Organization
from domain.policies.enterprise import GovernanceValidationError, SubjectContext
from infrastructure.security.rls_context import set_subject_governance_context
from app.support.errors import AppError
from app.api.routers.auth import authenticated_subject
from infrastructure.persistence.database import get_session


router = APIRouter()


async def authenticated_node(
    session: Annotated[AsyncSession, Depends(get_session)],
    node_token: Annotated[str | None, Header(alias="X-Assistant-Node")] = None,
) -> DepartmentNode:
    """Resolve a revocable node credential without accepting client tenant claims."""
    try:
        return await DepartmentNodeOperationsService(session).authenticate_node(node_token or "")
    except GovernanceValidationError as exc:
        raise AppError(
            code="node_authentication_required",
            message="Node authentication failed.",
            status_code=401,
        ) from exc


@router.get("/api/me", response_model=MeResponse)
async def get_me(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    """Return server-resolved local identity under the authenticated API boundary.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
    """
    await set_subject_governance_context(session, subject)
    organizations = tuple(
        await session.scalars(select(Organization).where(Organization.id.in_(subject.organization_ids)))
    ) if subject.organization_ids else ()
    return MeResponse(
        tenant_id=subject.tenant_id,
        user_id=subject.user_id,
        organization_ids=list(subject.organization_ids),
        organization_names=[organization.name for organization in organizations],
        roles=[role.value for role in subject.roles],
        authority_revision=subject.authority_revision,
    )


@router.get("/api/me/agent-profile", response_model=MyCapabilitiesResponse)
@router.get("/api/me/capabilities", response_model=MyCapabilitiesResponse)
async def get_my_capabilities(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MyCapabilitiesResponse:
    """Resolve effective capabilities freshly without exposing management controls.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
    """
    profile = await GovernedAgentProfileResolver(session).resolve_for_user(subject.user_id)
    await set_subject_governance_context(session, profile.subject)
    capability_details = await _effective_capability_details(
        session,
        tenant_id=profile.subject.tenant_id,
        capabilities=profile.capabilities,
    )
    return MyCapabilitiesResponse(
        schema_version=profile.schema_version,
        authority_revision=profile.subject.authority_revision,
        capabilities=list(profile.capabilities),
        capability_details=capability_details,
        tools=list(profile.tools),
        knowledge_scopes=list(profile.knowledge_scopes),
        memory_access=[list(item) for item in profile.memory_access],
        skills=[
            AuthorizedSkillResponse(
                name=name,
                display_name=name.replace("-", " ").title(),
                source="system" if name in {"skill-creator", "skill-installer"} else "curated-marketplace",
            )
            for name in profile.skills
        ],
    )


@router.post(
    "/api/me/capability-requests",
    response_model=CapabilityRequestResponse,
    status_code=201,
)
async def create_my_capability_request(
    payload: CapabilityRequestCreateRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CapabilityRequestResponse:
    """Create one employee-owned request without changing effective authority."""
    try:
        await set_subject_governance_context(session, subject)
        item = await CapabilityRequestService(session).create(
            subject,
            title=payload.title,
            description=payload.description,
            related_task_id=payload.related_task_id,
            idempotency_key=idempotency_key or "",
        )
        await session.commit()
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(
            code="capability_request_denied",
            message="Capability request could not be created.",
            status_code=400,
        ) from exc
    return CapabilityRequestResponse.model_validate(item)


@router.get(
    "/api/me/capability-requests",
    response_model=CapabilityRequestListResponse,
)
async def list_my_capability_requests(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CapabilityRequestListResponse:
    """List only the current authenticated employee's requests."""
    await set_subject_governance_context(session, subject)
    items = await CapabilityRequestService(session).list_personal(subject)
    return CapabilityRequestListResponse(
        items=[CapabilityRequestResponse.model_validate(item) for item in items]
    )


async def _effective_capability_details(
    session: AsyncSession,
    *,
    tenant_id: str,
    capabilities: tuple[str, ...],
) -> list[EffectiveCapabilityResponse]:
    """Resolve display metadata only for capabilities already in the trusted profile.

    Args:
        session: 当前数据库异步会话。
        tenant_id: 用于执行当前操作的 tenant id 参数。
        capabilities: 用于执行当前操作的 capabilities 参数。
    """
    requested = {
        tuple(value.split("@", maxsplit=1))
        for value in capabilities
        if "@" in value
    }
    if not requested:
        return []
    definitions = tuple(
        await session.scalars(
            select(CapabilityDefinition).where(
                CapabilityDefinition.tenant_id == tenant_id,
                CapabilityDefinition.status == "active",
            )
        )
    )
    matching = {
        (definition.key, definition.version): definition
        for definition in definitions
        if (definition.key, definition.version) in requested
    }
    return [
        EffectiveCapabilityResponse(
            id=capability,
            display_name=matching[(key, version)].display_name,
            summary=matching[(key, version)].summary,
        )
        for capability in capabilities
        if "@" in capability
        for key, version in (tuple(capability.split("@", maxsplit=1)),)
        if (key, version) in matching
    ]


@router.get("/api/admin/organizations", response_model=AdminListResponse)
async def list_admin_organizations(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminListResponse:
    """List tenant organizations for a server-resolved administrator.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
    """
    try:
        await set_subject_governance_context(session, subject)
        items = await EnterpriseAdminReadService(session).list_organizations(subject)
    except GovernanceValidationError as exc:
        raise AppError(code="admin_forbidden", message="Administrative access denied.", status_code=403) from exc
    return AdminListResponse(items=list(items))


@router.get(
    "/api/admin/capability-requests",
    response_model=CapabilityRequestListResponse,
)
async def list_admin_capability_requests(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[str | None, Query(max_length=32)] = None,
) -> CapabilityRequestListResponse:
    """List the tenant request queue for explicit IT capability roles."""
    try:
        await set_subject_governance_context(session, subject)
        items = await CapabilityRequestService(session).list_admin(subject, status=status)
    except GovernanceValidationError as exc:
        raise AppError(
            code="admin_forbidden",
            message="Administrative access denied.",
            status_code=403,
        ) from exc
    return CapabilityRequestListResponse(
        items=[CapabilityRequestResponse.model_validate(item) for item in items]
    )


@router.patch(
    "/api/admin/capability-requests/{request_id}/status",
    response_model=CapabilityRequestResponse,
)
async def update_admin_capability_request_status(
    request_id: str,
    payload: CapabilityRequestStatusUpdateRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CapabilityRequestResponse:
    """Advance one tenant request through the validated lifecycle."""
    operation = f"capability_request.status.{request_id}"
    try:
        await set_subject_governance_context(session, subject)
        mutations = EnterpriseAdminWriteService(session)
        prior = await mutations.existing_mutation(
            subject, idempotency_key=idempotency_key or "", operation=operation,
        )
        if prior is not None:
            return CapabilityRequestResponse.model_validate(prior)
        item = await CapabilityRequestService(session).update_status(
            subject, request_id=request_id, status=payload.status,
        )
        await mutations.save_mutation(
            subject,
            idempotency_key=idempotency_key or "",
            operation=operation,
            response=item,
        )
        await session.commit()
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(
            code="capability_request_update_denied",
            message="Capability request status update denied.",
            status_code=403,
        ) from exc
    return CapabilityRequestResponse.model_validate(item)


@router.get("/api/admin/members", response_model=AdminListResponse)
async def list_admin_members(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminListResponse:
    """List member identities and assignments visible to the server-resolved role.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
    """
    try:
        await set_subject_governance_context(session, subject)
        items = await EnterpriseAdminReadService(session).list_members(subject)
    except GovernanceValidationError as exc:
        raise AppError(
            code="admin_forbidden",
            message="Administrative access denied.",
            status_code=403,
        ) from exc
    return AdminListResponse(items=list(items))


@router.get("/api/admin/capabilities", response_model=AdminListResponse)
async def list_admin_capabilities(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminListResponse:
    """List approved capability catalog versions without exposing Skill content.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
    """
    try:
        await set_subject_governance_context(session, subject)
        items = await EnterpriseAdminReadService(session).list_capabilities(subject)
    except GovernanceValidationError as exc:
        raise AppError(
            code="admin_forbidden",
            message="Administrative access denied.",
            status_code=403,
        ) from exc
    return AdminListResponse(items=list(items))


@router.get("/api/admin/organizations/{organization_id}/capabilities", response_model=AdminListResponse)
async def list_admin_organization_capabilities(
    organization_id: str,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminListResponse:
    """List the capabilities currently granted to one department.

    Args:
        organization_id: 目标部门 ID。
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
    """
    try:
        await set_subject_governance_context(session, subject)
        items = await EnterpriseAdminReadService(session).list_organization_capabilities(
            subject, organization_id=organization_id
        )
    except GovernanceValidationError as exc:
        raise AppError(
            code="admin_forbidden",
            message="Administrative access denied.",
            status_code=403,
        ) from exc
    return AdminListResponse(items=list(items))


@router.get("/api/admin/audit", response_model=AdminListResponse)
async def list_admin_audit(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> AdminListResponse:
    """List sanitized tenant audit facts for explicit audit roles.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
        limit: 返回结果的最大数量。
    """
    try:
        await set_subject_governance_context(session, subject)
        items = await EnterpriseAdminReadService(session).list_audit(subject, limit=limit)
    except GovernanceValidationError as exc:
        raise AppError(code="admin_forbidden", message="Administrative access denied.", status_code=403) from exc
    return AdminListResponse(items=list(items))


@router.post("/api/admin/members", response_model=AdminListResponse, status_code=201)
async def provision_admin_member(
    payload: AdminMemberProvisionRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdminListResponse:
    """Create a local employee credential and its first governed department role.

    Args:
        payload: 当前操作的结构化载荷。
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
        idempotency_key: 用于识别重复请求的幂等键。
    """
    try:
        await set_subject_governance_context(session, subject)
        service = EnterpriseAdminWriteService(session)
        prior = await service.existing_mutation(
            subject, idempotency_key=idempotency_key or "", operation="member.provision"
        )
        if prior is not None:
            return AdminListResponse(items=[prior])
        item = await service.provision_member(
            subject,
            display_name=payload.display_name,
            login_name=payload.login_name,
            password=payload.password,
            organization_id=payload.organization_id,
            role=payload.role,
        )
        await service.save_mutation(
            subject,
            idempotency_key=idempotency_key or "",
            operation="member.provision",
            response=item,
        )
        await session.commit()
    except (GovernanceValidationError, LocalAuthError) as exc:
        await session.rollback()
        status_code = exc.status_code if isinstance(exc, LocalAuthError) else 403
        raise AppError(
            code="member_provision_denied",
            message="Member provisioning denied.",
            status_code=status_code,
        ) from exc
    return AdminListResponse(items=[item])


@router.post("/api/admin/node-enrollments", response_model=NodeEnrollmentResponse, status_code=201)
async def create_admin_node_enrollment(
    payload: NodeEnrollmentCreateRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NodeEnrollmentResponse:
    """Create one single-use department enrollment through enterprise authority."""
    try:
        await set_subject_governance_context(session, subject)
        item = await DepartmentNodeOperationsService(session).create_enrollment(
            subject, organization_id=payload.organization_id,
            expires_in_minutes=payload.expires_in_minutes,
        )
        await session.commit()
        return NodeEnrollmentResponse.model_validate(item)
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="node_enrollment_denied", message="Node enrollment denied.", status_code=403) from exc


@router.post("/api/admin/node-enrollments/{enrollment_id}/revoke", response_model=NodeEnrollmentStateResponse)
async def revoke_admin_node_enrollment(
    enrollment_id: str,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NodeEnrollmentStateResponse:
    """Revoke one unused same-tenant enrollment without exposing its secret."""
    try:
        await set_subject_governance_context(session, subject)
        item = await DepartmentNodeOperationsService(session).revoke_enrollment(
            subject, enrollment_id=enrollment_id
        )
        await session.commit()
        return NodeEnrollmentStateResponse.model_validate(item)
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="node_enrollment_denied", message="Node enrollment revocation denied.", status_code=403) from exc


@router.post("/api/nodes/register", response_model=NodeRegistrationResponse, status_code=201)
async def register_department_node(
    payload: NodeRegistrationRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NodeRegistrationResponse:
    """Consume one opaque enrollment without trusting tenant or department input."""
    try:
        item = await DepartmentNodeOperationsService(session).register_node(
            enrollment_token=payload.enrollment_token, name=payload.name,
            agent_version=payload.agent_version,
        )
        await session.commit()
        return NodeRegistrationResponse.model_validate(item)
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="node_registration_denied", message="Node registration failed.", status_code=400) from exc


@router.get("/api/admin/nodes", response_model=DepartmentNodeListResponse)
async def list_admin_nodes(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DepartmentNodeListResponse:
    """List safe node inventory facts within administrator organization scope."""
    try:
        await set_subject_governance_context(session, subject)
        items = await DepartmentNodeOperationsService(session).list_nodes(subject)
        return DepartmentNodeListResponse(
            items=[DepartmentNodeResponse.model_validate(item) for item in items]
        )
    except GovernanceValidationError as exc:
        raise AppError(code="node_inventory_denied", message="Node inventory access denied.", status_code=403) from exc


@router.post("/api/admin/nodes/{node_id}/revoke", response_model=DepartmentNodeListResponse)
async def revoke_admin_node(
    node_id: str,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DepartmentNodeListResponse:
    """Revoke one same-tenant node and its credential through enterprise authority."""
    try:
        await set_subject_governance_context(session, subject)
        item = await DepartmentNodeOperationsService(session).revoke_node(subject, node_id=node_id)
        await session.commit()
        return DepartmentNodeListResponse(items=[DepartmentNodeResponse.model_validate(item)])
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="node_revocation_denied", message="Node revocation denied.", status_code=403) from exc


@router.post("/api/nodes/heartbeat", response_model=DepartmentNodeListResponse)
async def record_node_heartbeat(
    payload: NodeHeartbeatRequest,
    node: Annotated[DepartmentNode, Depends(authenticated_node)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DepartmentNodeListResponse:
    """Record bounded health state from one authenticated active node."""
    try:
        item = await DepartmentNodeOperationsService(session).heartbeat(
            node, agent_version=payload.agent_version,
            applied_config_revision=payload.applied_config_revision,
            accepts_new_work=payload.accepts_new_work,
            health_status=payload.health_status, health_summary=payload.health_summary,
        )
        await session.commit()
        return DepartmentNodeListResponse(items=[DepartmentNodeResponse.model_validate(item)])
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="node_heartbeat_denied", message="Node heartbeat rejected.", status_code=400) from exc


@router.get("/api/nodes/configuration", response_model=NodeConfigurationResponse)
async def get_node_configuration(
    node: Annotated[DepartmentNode, Depends(authenticated_node)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NodeConfigurationResponse:
    """Return only the node department's canonical governed configuration."""
    item = await DepartmentNodeOperationsService(session).desired_configuration(node)
    await session.commit()
    return NodeConfigurationResponse.model_validate(item)


@router.post("/api/nodes/configuration/ack", response_model=NodeConfigAckResponse)
async def acknowledge_node_configuration(
    payload: NodeConfigAckRequest,
    node: Annotated[DepartmentNode, Depends(authenticated_node)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NodeConfigAckResponse:
    """Persist one apply result without losing the node's last-known-good revision."""
    try:
        item = await DepartmentNodeOperationsService(session).acknowledge_configuration(
            node, revision=payload.revision, status=payload.status,
            error_code=payload.error_code, error_summary=payload.error_summary,
        )
        await session.commit()
        return NodeConfigAckResponse.model_validate(item)
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="node_configuration_ack_denied", message="Configuration acknowledgement rejected.", status_code=400) from exc


@router.put("/api/admin/members/{user_id}/membership", response_model=AdminListResponse)
async def assign_admin_member_role(
    user_id: str,
    payload: AdminMemberRoleAssignmentRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdminListResponse:
    """Assign a permitted role in one organization to an existing employee.

    Args:
        user_id: 目标用户 ID。
        payload: 当前操作的结构化载荷。
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
        idempotency_key: 用于识别重复请求的幂等键。
    """
    try:
        await set_subject_governance_context(session, subject)
        service = EnterpriseAdminWriteService(session)
        prior = await service.existing_mutation(
            subject, idempotency_key=idempotency_key or "", operation="member.role.assign"
        )
        if prior is not None:
            return AdminListResponse(items=[prior])
        item = await service.assign_member_role(
            subject,
            user_id=user_id,
            organization_id=payload.organization_id,
            role=payload.role,
        )
        await service.save_mutation(
            subject,
            idempotency_key=idempotency_key or "",
            operation="member.role.assign",
            response=item,
        )
        await session.commit()
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(
            code="member_assignment_denied",
            message="Member role assignment denied.",
            status_code=403,
        ) from exc
    return AdminListResponse(items=[item])


@router.post("/api/admin/organizations", response_model=AdminListResponse, status_code=201)
async def create_admin_organization(
    payload: AdminOrganizationCreateRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdminListResponse:
    """执行 create admin organization 操作。

    Args:
        payload: 当前操作的结构化载荷。
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
        idempotency_key: 用于识别重复请求的幂等键。
    """
    try:
        await set_subject_governance_context(session, subject)
        service = EnterpriseAdminWriteService(session)
        prior = await service.existing_mutation(subject, idempotency_key=idempotency_key or "", operation="organization.create")
        if prior is not None:
            return AdminListResponse(items=[prior])
        item = await service.create_organization(
            subject, name=payload.name, organization_type=payload.type, parent_id=payload.parent_id
        )
        await service.save_mutation(subject, idempotency_key=idempotency_key or "", operation="organization.create", response=item)
        await session.commit()
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="admin_forbidden", message="Administrative write denied.", status_code=403) from exc
    return AdminListResponse(items=[item])


@router.post("/api/admin/departments/bootstrap", response_model=AdminListResponse)
async def bootstrap_admin_departments(
    request: Request,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdminListResponse:
    """Create the governed 财务部 and 市场部 templates without creating IT.

    Args:
        request: 当前操作的结构化请求。
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
        idempotency_key: 用于识别重复请求的幂等键。
    """
    try:
        await set_subject_governance_context(session, subject)
        service = EnterpriseAdminWriteService(session)
        prior = await service.existing_mutation(
            subject, idempotency_key=idempotency_key or "", operation="departments.bootstrap"
        )
        if prior is not None:
            return AdminListResponse(items=[prior])
        result = await DepartmentBootstrapService(
            session, capability_registry=request.app.state.capability_registry
        ).bootstrap(subject)
        await service.save_mutation(
            subject,
            idempotency_key=idempotency_key or "",
            operation="departments.bootstrap",
            response=result,
        )
        await session.commit()
        return AdminListResponse(items=[result])
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="admin_forbidden", message="Department bootstrap denied.", status_code=403) from exc


@router.post(
    "/api/admin/skill-candidates",
    response_model=AdminListResponse,
    status_code=201,
)
async def record_admin_marketplace_skill_candidate(
    payload: AdminMarketplaceSkillCandidateRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdminListResponse:
    """Record metadata-only marketplace Skill intake without downloading packages.

    Args:
        payload: 当前操作的结构化载荷。
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
        idempotency_key: 用于识别重复请求的幂等键。
    """
    try:
        await set_subject_governance_context(session, subject)
        service = EnterpriseAdminWriteService(session)
        prior = await service.existing_mutation(
            subject,
            idempotency_key=idempotency_key or "",
            operation="skill.candidate.record",
        )
        if prior is not None:
            return AdminListResponse(items=[prior])
        item = await MarketplaceSkillCandidateService(session).record(
            subject,
            key=payload.key,
            display_name=payload.display_name,
            summary=payload.summary,
        )
        await service.save_mutation(
            subject,
            idempotency_key=idempotency_key or "",
            operation="skill.candidate.record",
            response=item,
        )
        await session.commit()
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(
            code="admin_forbidden",
            message="Marketplace Skill candidate denied.",
            status_code=403,
        ) from exc
    return AdminListResponse(items=[item])


@router.post("/api/admin/connectors", response_model=AdminListResponse, status_code=201)
async def create_admin_connector(
    payload: AdminConnectorCreateRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdminListResponse:
    """执行 create admin connector 操作。

    Args:
        payload: 当前操作的结构化载荷。
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
        idempotency_key: 用于识别重复请求的幂等键。
    """
    try:
        await set_subject_governance_context(session, subject)
        service = EnterpriseAdminWriteService(session)
        prior = await service.existing_mutation(subject, idempotency_key=idempotency_key or "", operation="connector.create")
        if prior is not None:
            return AdminListResponse(items=[prior])
        item = await service.create_connector(
            subject,
            key=payload.key,
            connector_type=payload.connector_type,
            display_name=payload.display_name,
            config=payload.config,
        )
        await service.save_mutation(subject, idempotency_key=idempotency_key or "", operation="connector.create", response=item)
        await session.commit()
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="admin_forbidden", message="Administrative write denied.", status_code=403) from exc
    return AdminListResponse(items=[item])


@router.post("/api/admin/connectors/{connector_id}/instances", response_model=AdminListResponse, status_code=201)
async def create_admin_connector_instance(
    connector_id: str,
    payload: AdminConnectorInstanceCreateRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdminListResponse:
    """执行 create admin connector instance 操作。

    Args:
        connector_id: 用于执行当前操作的 connector id 参数。
        payload: 当前操作的结构化载荷。
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
        idempotency_key: 用于识别重复请求的幂等键。
    """
    try:
        await set_subject_governance_context(session, subject)
        service = EnterpriseAdminWriteService(session)
        prior = await service.existing_mutation(subject, idempotency_key=idempotency_key or "", operation="connector.instance.create")
        if prior is not None:
            return AdminListResponse(items=[prior])
        item = await service.create_connector_instance(
            subject,
            connector_id=connector_id,
            organization_id=payload.organization_id,
            auth_mode=payload.auth_mode,
            credential_ref=payload.credential_ref,
        )
        await service.save_mutation(subject, idempotency_key=idempotency_key or "", operation="connector.instance.create", response=item)
        await session.commit()
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="admin_forbidden", message="Administrative write denied.", status_code=403) from exc
    return AdminListResponse(items=[item])


@router.post("/api/admin/connector-tools/{tool_id}/review", response_model=AdminListResponse)
async def review_admin_connector_tool(
    tool_id: str,
    payload: AdminConnectorToolReviewRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdminListResponse:
    """执行 review admin connector tool 操作。

    Args:
        tool_id: 用于执行当前操作的 tool id 参数。
        payload: 当前操作的结构化载荷。
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
        idempotency_key: 用于识别重复请求的幂等键。
    """
    try:
        await set_subject_governance_context(session, subject)
        service = EnterpriseAdminWriteService(session)
        prior = await service.existing_mutation(subject, idempotency_key=idempotency_key or "", operation="connector.tool.review")
        if prior is not None:
            return AdminListResponse(items=[prior])
        item = await service.review_connector_tool(
            subject,
            tool_id=tool_id,
            internal_tool_key=payload.internal_tool_key,
            risk_level=payload.risk_level,
            enabled=payload.enabled,
        )
        await service.save_mutation(subject, idempotency_key=idempotency_key or "", operation="connector.tool.review", response=item)
        await session.commit()
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="admin_forbidden", message="Administrative write denied.", status_code=403) from exc
    return AdminListResponse(items=[item])


@router.post("/api/admin/capabilities/{capability_version_id}/distribute", response_model=AdminListResponse)
async def distribute_admin_capability(
    capability_version_id: str,
    payload: AdminCapabilityDistributionRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AdminListResponse:
    """执行 distribute admin capability 操作。

    Args:
        capability_version_id: 用于执行当前操作的 capability version id 参数。
        payload: 当前操作的结构化载荷。
        subject: 用于执行当前操作的 subject 参数。
        session: 当前数据库异步会话。
        idempotency_key: 用于识别重复请求的幂等键。
    """
    try:
        await set_subject_governance_context(session, subject)
        service = EnterpriseAdminWriteService(session)
        prior = await service.existing_mutation(subject, idempotency_key=idempotency_key or "", operation="capability.distribute")
        if prior is not None:
            return AdminListResponse(items=[prior])
        item = await service.distribute_capability(
            subject,
            capability_version_id=capability_version_id,
            organization_id=payload.organization_id,
            can_delegate=payload.can_delegate,
        )
        await service.save_mutation(subject, idempotency_key=idempotency_key or "", operation="capability.distribute", response=item)
        await session.commit()
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="admin_forbidden", message="Administrative write denied.", status_code=403) from exc
    return AdminListResponse(items=[item])


@router.get("/api/admin/diagnostics", response_model=DiagnosticRecordListResponse)
async def list_admin_diagnostics(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    start_at: datetime,
    end_at: datetime,
    organization_id: str | None = None,
    node_id: str | None = None,
    task_id: str | None = None,
    status: str | None = None,
    capability_version: str | None = None,
) -> DiagnosticRecordListResponse:
    """Query bounded redacted diagnostics under tenant and organization authorization."""
    try:
        await set_subject_governance_context(session, subject)
        items = await DepartmentNodeOperationsService(session).query_diagnostics(
            subject, start_at=start_at, end_at=end_at,
            organization_id=organization_id, node_id=node_id, task_id=task_id,
            status=status, capability_version=capability_version,
        )
        return DiagnosticRecordListResponse(
            items=[DiagnosticRecordResponse.model_validate(item) for item in items]
        )
    except GovernanceValidationError as exc:
        raise AppError(code="diagnostic_query_denied", message="Diagnostic query denied.", status_code=403) from exc


@router.post("/api/admin/diagnostic-packages", response_model=DiagnosticPackageListResponse, status_code=201)
async def create_admin_diagnostic_package(
    payload: DiagnosticPackageCreateRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DiagnosticPackageListResponse:
    """Create one immutable expiring redacted diagnostic manifest."""
    try:
        await set_subject_governance_context(session, subject)
        item = await DepartmentNodeOperationsService(session).create_diagnostic_package(
            subject, start_at=payload.start_at, end_at=payload.end_at,
            expires_in_minutes=payload.expires_in_minutes,
            organization_id=payload.organization_id, node_id=payload.node_id,
            task_id=payload.task_id, status=payload.status,
            capability_version=payload.capability_version,
        )
        await session.commit()
        return DiagnosticPackageListResponse(
            items=[DiagnosticPackageResponse.model_validate(item)]
        )
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="diagnostic_package_denied", message="Diagnostic package creation denied.", status_code=403) from exc


@router.get("/api/admin/diagnostic-packages", response_model=DiagnosticPackageListResponse)
async def list_admin_diagnostic_packages(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DiagnosticPackageListResponse:
    """List safe package metadata without returning stored manifest contents."""
    try:
        items = await DepartmentNodeOperationsService(session).list_diagnostic_packages(subject)
        return DiagnosticPackageListResponse(
            items=[DiagnosticPackageResponse.model_validate(item) for item in items]
        )
    except GovernanceValidationError as exc:
        raise AppError(code="diagnostic_package_denied", message="Diagnostic package access denied.", status_code=403) from exc


@router.get("/api/admin/diagnostic-packages/{package_id}/download")
async def download_admin_diagnostic_package(
    package_id: str,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Download an unexpired redacted JSON package and record the access audit."""
    try:
        content = await DepartmentNodeOperationsService(session).download_diagnostic_package(subject, package_id)
        await session.commit()
        return Response(
            content=content, media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="diagnostic-{package_id}.json"'},
        )
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="diagnostic_package_denied", message="Diagnostic package access denied.", status_code=403) from exc


@router.post("/api/admin/nodes/{node_id}/operations", response_model=NodeOperationListResponse, status_code=201)
async def create_admin_node_operation(
    node_id: str,
    payload: NodeOperationCreateRequest,
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> NodeOperationListResponse:
    """Queue one whitelisted expiring node operation through administrator authority."""
    try:
        await set_subject_governance_context(session, subject)
        item = await DepartmentNodeOperationsService(session).create_operation(
            subject, node_id=node_id, operation_type=payload.operation_type,
            target_task_id=payload.target_task_id,
            idempotency_key=idempotency_key or "",
            expires_in_minutes=payload.expires_in_minutes,
        )
        await session.commit()
        return NodeOperationListResponse(items=[NodeOperationResponse.model_validate(item)])
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="node_operation_denied", message="Node operation denied.", status_code=403) from exc


@router.get("/api/admin/node-operations", response_model=NodeOperationListResponse)
async def list_admin_node_operations(
    subject: Annotated[SubjectContext, Depends(authenticated_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NodeOperationListResponse:
    """List safe remote-operation ledger facts visible to the administrator."""
    try:
        items = await DepartmentNodeOperationsService(session).list_operations(subject)
        return NodeOperationListResponse(
            items=[NodeOperationResponse.model_validate(item) for item in items]
        )
    except GovernanceValidationError as exc:
        raise AppError(code="node_operation_denied", message="Node operation access denied.", status_code=403) from exc


@router.get("/api/nodes/operations", response_model=NodeOperationListResponse)
async def poll_node_operations(
    node: Annotated[DepartmentNode, Depends(authenticated_node)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NodeOperationListResponse:
    """Deliver only this authenticated node's unexpired queued operations."""
    items = await DepartmentNodeOperationsService(session).poll_operations(node)
    await session.commit()
    return NodeOperationListResponse(
        items=[NodeOperationResponse.model_validate(item) for item in items]
    )


@router.post("/api/nodes/operations/{operation_id}/ack", response_model=NodeOperationListResponse)
async def acknowledge_node_operation(
    operation_id: str,
    payload: NodeOperationAckRequest,
    node: Annotated[DepartmentNode, Depends(authenticated_node)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NodeOperationListResponse:
    """Complete one delivered operation idempotently with a bounded node result."""
    try:
        item = await DepartmentNodeOperationsService(session).acknowledge_operation(
            node, operation_id=operation_id, status=payload.status,
            result_code=payload.result_code, result_summary=payload.result_summary,
        )
        await session.commit()
        return NodeOperationListResponse(items=[NodeOperationResponse.model_validate(item)])
    except GovernanceValidationError as exc:
        await session.rollback()
        raise AppError(code="node_operation_ack_denied", message="Node operation acknowledgement denied.", status_code=400) from exc
