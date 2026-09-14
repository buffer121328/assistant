from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Mapping, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.local_auth import LocalAuthenticationService
from domain.models import (
    CapabilityDefinition,
    CapabilityGrant,
    CapabilityVersion,
    Connector,
    ConnectorInstance,
    ConnectorTool,
    GovernanceAudit,
    Organization,
    OrganizationMembership,
    Tenant,
    User,
)
from infrastructure.security.rls_context import set_subject_governance_context

from domain.policies.enterprise import (
    GovernedAgentProfile,
    GovernedRole,
    GovernanceValidationError,
    PolicyDecision,
    PolicyEffect,
    ResourceScope,
    ResourceVisibility,
    SubjectContext,
    resource_scope_allows,
)


def _capability_skill_names(bindings_json: str) -> list[str]:
    try:
        value = json.loads(bindings_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    skills = value.get("skills", []) if isinstance(value, dict) else []
    return [str(item) for item in skills if isinstance(item, str)][:64]


@dataclass(frozen=True)
class GovernedRetrievalFilter:
    """定义当前组件的职责和边界。"""

    tenant_id: str  # tenant_id 对应的数据字段。
    organization_ids: tuple[str, ...]  # organization_ids 对应的数据字段。
    owner_id: str  # owner_id 对应的数据字段。
    allowed_scopes: tuple[str, ...]  # allowed_scopes 对应的数据字段。
    allowed_classifications: tuple[str, ...] = ("public", "internal")  # allowed_classifications 对应的数据字段。

    def provider_filter(self) -> dict[str, object]:
        """执行当前组件定义的业务处理逻辑。"""
        return {
            "tenant_id": self.tenant_id,
            "organization_ids": self.organization_ids,
            "owner_id": self.owner_id,
            "scopes": self.allowed_scopes,
            "classifications": self.allowed_classifications,
        }

    def allows(self, scope: ResourceScope, *, classification: str = "internal") -> bool:
        """Check one recalled resource against the same governed boundary.

        Args:
            scope: 用于执行当前操作的 scope 参数。
            classification: 用于执行当前操作的 classification 参数。
        """
        if classification not in self.allowed_classifications:
            return False
        scope_name = {
            ResourceVisibility.PRIVATE: "personal",
            ResourceVisibility.DEPARTMENT: "department",
            ResourceVisibility.ORGANIZATION: "organization",
        }[scope.visibility]
        subject = SubjectContext(
            tenant_id=self.tenant_id,
            user_id=self.owner_id,
            organization_ids=self.organization_ids,
        )
        return scope_name in self.allowed_scopes and resource_scope_allows(subject, scope)


def memory_retrieval_filter(profile: GovernedAgentProfile) -> GovernedRetrievalFilter:
    """Build memory scope constraints from a fresh governed profile.

    Args:
        profile: 用于执行当前操作的 profile 参数。
    """
    scopes = tuple(scope for scope, access in profile.memory_access if access != "none")
    return GovernedRetrievalFilter(
        tenant_id=profile.subject.tenant_id,
        organization_ids=profile.subject.organization_ids,
        owner_id=profile.subject.user_id,
        allowed_scopes=scopes,
    )


def knowledge_retrieval_filter(profile: GovernedAgentProfile) -> GovernedRetrievalFilter:
    """Build knowledge constraints without accepting client-selected collections.

    Args:
        profile: 用于执行当前操作的 profile 参数。
    """
    scopes = tuple(dict.fromkeys(_scope_prefix(item) for item in profile.knowledge_scopes))
    return GovernedRetrievalFilter(
        tenant_id=profile.subject.tenant_id,
        organization_ids=profile.subject.organization_ids,
        owner_id=profile.subject.user_id,
        allowed_scopes=scopes,
    )


def _scope_prefix(value: str) -> str:
    """Map stable knowledge scope identifiers onto resource visibility scopes.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    prefix = value.split(".", 1)[0].lower()
    return prefix if prefix in {"personal", "department", "organization"} else "personal"


class GovernedSemanticProvider(Protocol):
    """定义当前组件的接口契约。"""

    async def search(
        self, *, query: str, filters: Mapping[str, object], limit: int
    ) -> tuple[Mapping[str, object], ...]:
        """Return candidates restricted by the supplied governed filter.

        Args:
            query: 用于执行当前操作的 query 参数。
            filters: 用于执行当前操作的 filters 参数。
            limit: 返回结果的最大数量。
        """
        ...


async def governed_semantic_search(
    provider: GovernedSemanticProvider,
    *,
    query: str,
    access_filter: GovernedRetrievalFilter,
    limit: int,
    audit_session: AsyncSession | None = None,
    provider_key: str = "semantic",
) -> tuple[Mapping[str, object], ...]:
    """Invoke semantic recall only with a non-empty governed filter.

    Args:
        provider: 用于执行当前操作的 provider 参数。
        query: 用于执行当前操作的 query 参数。
        access_filter: 用于执行当前操作的 access filter 参数。
        limit: 返回结果的最大数量。
        audit_session: 用于执行当前操作的 audit session 参数。
        provider_key: 用于执行当前操作的 provider key 参数。
    """
    if not access_filter.tenant_id or not access_filter.allowed_scopes:
        await _record_retrieval_audit(audit_session, access_filter, provider_key, "DENY", "missing_scope")
        return ()
    if getattr(provider, "supports_governed_filters", True) is False:
        await _record_retrieval_audit(audit_session, access_filter, provider_key, "DENY", "unsupported_filter")
        return ()
    results = await provider.search(
        query=query[:2_000], filters=access_filter.provider_filter(), limit=max(1, min(limit, 20))
    )
    await _record_retrieval_audit(audit_session, access_filter, provider_key, "ALLOW", "retrieval_succeeded")
    return results


async def _record_retrieval_audit(
    session: AsyncSession | None,
    access_filter: GovernedRetrievalFilter,
    provider_key: str,
    policy_decision: str,
    result_status: str,
) -> None:
    """执行 record retrieval audit 的内部处理逻辑。

    Args:
        session: 当前数据库异步会话。
        access_filter: 用于执行当前操作的 access filter 参数。
        provider_key: 用于执行当前操作的 provider key 参数。
        policy_decision: 用于执行当前操作的 policy decision 参数。
        result_status: 用于执行当前操作的 result status 参数。
    """
    if session is None:
        return
    await GovernanceAuditService(session).record(
        GovernanceAuditInput(
            tenant_id=access_filter.tenant_id,
            subject_id=access_filter.owner_id,
            organization_id=access_filter.organization_ids[0] if access_filter.organization_ids else None,
            provider_key=provider_key[:128],
            resource_type="semantic_retrieval",
            policy_decision=policy_decision,
            result_status=result_status,
            summary="Governed semantic retrieval result",
        )
    )


@dataclass(frozen=True)
class EmployeeCapabilitySummary:
    """定义当前组件的职责和边界。"""

    tenant_id: str  # tenant_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    capabilities: tuple[str, ...]  # capabilities 对应的数据字段。
    tools: tuple[str, ...]  # tools 对应的数据字段。
    approval_required_tools: tuple[str, ...]  # approval_required_tools 对应的数据字段。


def employee_capability_summary(
    profile: GovernedAgentProfile,
    *,
    approval_required_tools: tuple[str, ...] = (),
) -> EmployeeCapabilitySummary:
    """Expose effective abilities without management or secret configuration.

    Args:
        profile: 用于执行当前操作的 profile 参数。
        approval_required_tools: 用于执行当前操作的 approval required tools 参数。
    """
    approved = tuple(tool for tool in approval_required_tools if tool in profile.tools)
    return EmployeeCapabilitySummary(
        profile.subject.tenant_id,
        profile.subject.user_id,
        profile.capabilities,
        profile.tools,
        approved,
    )


def require_admin_role(subject: SubjectContext, *allowed: GovernedRole) -> None:
    """Fail closed when a subject lacks an explicit role for an admin operation.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        allowed: 用于执行当前操作的 allowed 参数。
    """
    if not set(subject.roles).intersection(allowed):
        raise GovernanceValidationError("Administrative role required")


async def require_ai_department_administrator(
    session: AsyncSession,
    subject: SubjectContext,
) -> None:
    """Require a current AI department lead before executable authority expands.

    The lookup uses persisted same-tenant membership and organization facts rather
    than trusting role or department labels carried by a client.

    Args:
        session: Current governed database session.
        subject: Server-resolved acting subject.
    """
    membership_id = await session.scalar(
        select(OrganizationMembership.id)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.tenant_id == subject.tenant_id,
            OrganizationMembership.user_id == subject.user_id,
            OrganizationMembership.role == GovernedRole.DEPARTMENT_ADMIN.value,
            OrganizationMembership.status == "active",
            Organization.tenant_id == subject.tenant_id,
            Organization.name == "AI部",
            Organization.type == "department",
            Organization.status == "active",
        )
    )
    if membership_id is None:
        raise GovernanceValidationError("AI department administrator approval required")


def approval_choices(
    decision: PolicyDecision, *, persistent_allowed: bool = False
) -> tuple[str, ...]:
    """Return only policy-authorized user choices for an approval surface.

    Args:
        decision: 当前操作的治理决策。
        persistent_allowed: 用于执行当前操作的 persistent allowed 参数。
    """
    if decision.effect not in {PolicyEffect.CONFIRM, PolicyEffect.APPROVAL}:
        return ()
    choices = ("approve_once", "reject")
    return choices + (("allow_similar",) if persistent_allowed else ())


def argument_fingerprint(arguments: Mapping[str, object]) -> str:
    """Hash canonical arguments so audit and approval records avoid raw values.

    Args:
        arguments: 当前调用的结构化参数。
    """
    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def revalidate_approval(
    *,
    expected_fingerprint: str,
    arguments: Mapping[str, object],
    decision: PolicyDecision,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> None:
    """Recheck argument identity, expiry, and current policy before resume.

    Args:
        expected_fingerprint: 用于执行当前操作的 expected fingerprint 参数。
        arguments: 当前调用的结构化参数。
        decision: 当前操作的治理决策。
        expires_at: 用于执行当前操作的 expires at 参数。
        now: 用于执行当前操作的 now 参数。
    """
    current = now or datetime.now(UTC)
    expiry = expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if expiry is not None and expiry <= current:
        raise GovernanceValidationError("Approval expired")
    if argument_fingerprint(arguments) != expected_fingerprint:
        raise GovernanceValidationError("Approval arguments changed")
    if decision.effect not in {PolicyEffect.ALLOW, PolicyEffect.CONFIRM, PolicyEffect.APPROVAL}:
        raise GovernanceValidationError("Current policy denies approval")


@dataclass(frozen=True)
class GovernanceAuditInput:
    """定义当前组件的职责和边界。"""

    tenant_id: str  # tenant_id 对应的数据字段。
    subject_id: str  # subject_id 对应的数据字段。
    policy_decision: str  # policy_decision 对应的数据字段。
    result_status: str  # result_status 对应的数据字段。
    organization_id: str | None = None  # organization_id 对应的数据字段。
    conversation_id: str | None = None  # conversation_id 对应的数据字段。
    task_id: str | None = None  # task_id 对应的数据字段。
    run_id: str | None = None  # run_id 对应的数据字段。
    agent_id: str | None = None  # agent_id 对应的数据字段。
    capability_key: str | None = None  # capability_key 对应的数据字段。
    tool_key: str | None = None  # tool_key 对应的数据字段。
    provider_key: str | None = None  # provider_key 对应的数据字段。
    resource_type: str | None = None  # resource_type 对应的数据字段。
    resource_id: str | None = None  # resource_id 对应的数据字段。
    risk: str | None = None  # risk 对应的数据字段。
    approval_id: str | None = None  # approval_id 对应的数据字段。
    arguments_hash: str | None = None  # arguments_hash 对应的数据字段。
    model_name: str | None = None  # model_name 对应的数据字段。
    cost_usd: float | None = None  # cost_usd 对应的数据字段。
    summary: str = ""  # summary 对应的数据字段。


class GovernanceAuditService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """Use the caller transaction so protected actions can fail closed.

        Args:
            session: 当前数据库异步会话。
        """
        self._session = session

    async def record(self, fact: GovernanceAuditInput) -> GovernanceAudit:
        """Append one fact without accepting raw arguments or credential payloads.

        Args:
            fact: 用于执行当前操作的 fact 参数。
        """
        if not fact.tenant_id or not fact.subject_id or not fact.policy_decision:
            raise GovernanceValidationError("Incomplete governance audit fact")
        row = GovernanceAudit(
            **{
                **fact.__dict__,
                "summary": _redact_summary(fact.summary),
                "created_at": datetime.now(UTC),
            }
        )
        self._session.add(row)
        await self._session.flush()
        return row


def _redact_summary(value: str) -> str:
    """Bound summaries and remove common reusable credential markers.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    safe = value[:2_000]
    lowered = safe.lower()
    if any(marker in lowered for marker in ("authorization:", "bearer ", "password=", "token=")):
        return "[REDACTED]"
    return safe


class EnterpriseAdminReadService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """Bind reads to one transaction and server-resolved subject.

        Args:
            session: 当前数据库异步会话。
        """
        self._session = session

    async def list_organizations(self, subject: SubjectContext) -> tuple[dict[str, object], ...]:
        """List only organizations inside the administrator's tenant and scope.

        Args:
            subject: 用于执行当前操作的 subject 参数。
        """
        require_admin_role(
            subject,
            GovernedRole.ENTERPRISE_ADMIN,
            GovernedRole.DEPARTMENT_ADMIN,
            GovernedRole.AUDITOR,
        )
        statement = select(Organization).where(Organization.tenant_id == subject.tenant_id)
        if GovernedRole.ENTERPRISE_ADMIN not in subject.roles and subject.organization_ids:
            statement = statement.where(Organization.id.in_(subject.organization_ids))
        rows = tuple(await self._session.scalars(statement.order_by(Organization.name)))
        return tuple(
            {"id": row.id, "name": row.name, "type": row.type, "status": row.status}
            for row in rows
        )

    async def list_members(self, subject: SubjectContext) -> tuple[dict[str, object], ...]:
        """List tenant members only for the management roles allowed to inspect them.

        Args:
            subject: 用于执行当前操作的 subject 参数。
        """
        require_admin_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.DEPARTMENT_ADMIN)
        statement = (
            select(OrganizationMembership, User, Organization)
            .join(User, User.id == OrganizationMembership.user_id)
            .join(Organization, Organization.id == OrganizationMembership.organization_id)
            .where(OrganizationMembership.tenant_id == subject.tenant_id)
            .order_by(Organization.name, User.display_name)
        )
        if GovernedRole.ENTERPRISE_ADMIN not in subject.roles and subject.organization_ids:
            statement = statement.where(OrganizationMembership.organization_id.in_(subject.organization_ids))
        rows = tuple((await self._session.execute(statement)).all())
        return tuple(
            {
                "user_id": user.id,
                "display_name": user.display_name,
                "organization_id": organization.id,
                "organization_name": organization.name,
                "role": membership.role,
                "status": membership.status,
            }
            for membership, user, organization in rows
        )

    async def list_capabilities(self, subject: SubjectContext) -> tuple[dict[str, object], ...]:
        """List immutable active catalog versions without exposing Skill implementation payloads.

        Args:
            subject: 用于执行当前操作的 subject 参数。
        """
        require_admin_role(
            subject,
            GovernedRole.ENTERPRISE_ADMIN,
            GovernedRole.DEPARTMENT_ADMIN,
            GovernedRole.CAPABILITY_PUBLISHER,
        )
        rows = tuple(
            (await self._session.execute(
                select(CapabilityVersion, CapabilityDefinition)
                .join(CapabilityDefinition, CapabilityDefinition.id == CapabilityVersion.capability_id)
                .where(
                    CapabilityVersion.tenant_id == subject.tenant_id,
                    CapabilityDefinition.status == "active",
                )
                .order_by(CapabilityDefinition.display_name, CapabilityVersion.version)
            )).all()
        )
        # Department enablement facts stay inside the reader's grant-visibility scope:
        # enterprise admins see the whole tenant, others only their own memberships.
        visibility_all = GovernedRole.ENTERPRISE_ADMIN in subject.roles
        scoped_organization_ids = set(subject.organization_ids or [])
        enablement_rows = (await self._session.execute(
            select(CapabilityGrant.capability_id, CapabilityGrant.target_organization_id, Organization.name)
            .join(Organization, Organization.id == CapabilityGrant.target_organization_id)
            .where(
                CapabilityGrant.tenant_id == subject.tenant_id,
                CapabilityGrant.status == "active",
            )
        )).all()
        departments_by_capability: dict[str, set[str]] = {}
        for capability_id, target_organization_id, organization_name in enablement_rows:
            if not visibility_all and target_organization_id not in scoped_organization_ids:
                continue
            departments_by_capability.setdefault(capability_id, set()).add(organization_name)
        return tuple(
            {
                "capability_version_id": version.id,
                "capability_id": definition.id,
                "key": definition.key,
                "version": version.version,
                "display_name": definition.display_name,
                "summary": definition.summary,
                "skills": _capability_skill_names(definition.bindings_json),
                "departments": sorted(departments_by_capability.get(definition.id, set())),
            }
            for version, definition in rows
        )

    async def list_organization_capabilities(
        self, subject: SubjectContext, *, organization_id: str
    ) -> tuple[dict[str, object], ...]:
        """List active capability grants for one department without Skill payloads.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            organization_id: 目标部门 ID。
        """
        require_admin_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.DEPARTMENT_ADMIN)
        organization = await self._session.get(Organization, organization_id)
        if organization is None or organization.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Organization is unavailable")
        if (
            GovernedRole.ENTERPRISE_ADMIN not in subject.roles
            and organization.id not in subject.organization_ids
        ):
            raise GovernanceValidationError("Department capabilities are outside admin scope")
        rows = tuple(
            (await self._session.execute(
                select(CapabilityGrant, CapabilityDefinition)
                .join(CapabilityDefinition, CapabilityDefinition.id == CapabilityGrant.capability_id)
                .where(
                    CapabilityGrant.tenant_id == subject.tenant_id,
                    CapabilityGrant.target_organization_id == organization.id,
                    CapabilityGrant.status == "active",
                )
                .order_by(CapabilityGrant.created_at.desc())
            )).all()
        )
        # Keep the newest grant per capability so re-distributions collapse into one row.
        selected: dict[str, tuple[CapabilityGrant, CapabilityDefinition]] = {}
        for grant, definition in rows:
            current = selected.get(definition.id)
            if current is None or grant.created_at > current[0].created_at:
                selected[definition.id] = (grant, definition)
        return tuple(
            {
                "grant_id": grant.id,
                "capability_id": definition.id,
                "key": definition.key,
                "display_name": definition.display_name,
                "summary": definition.summary,
                "version": grant.version_constraint,
                "distributed_at": grant.created_at.isoformat(),
            }
            for grant, definition in sorted(
                selected.values(), key=lambda pair: pair[0].created_at, reverse=True
            )
        )

    async def list_audit(self, subject: SubjectContext, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        """Return bounded redacted audit facts to explicit audit roles.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            limit: 返回结果的最大数量。
        """
        require_admin_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.AUDITOR)
        statement = select(GovernanceAudit).where(GovernanceAudit.tenant_id == subject.tenant_id)
        if GovernedRole.ENTERPRISE_ADMIN not in subject.roles and subject.organization_ids:
            statement = statement.where(GovernanceAudit.organization_id.in_(subject.organization_ids))
        rows = tuple(
            await self._session.scalars(
                statement.order_by(GovernanceAudit.created_at.desc()).limit(max(1, min(limit, 200)))
            )
        )
        return tuple(
            {
                "id": row.id,
                "subject_id": row.subject_id,
                "task_id": row.task_id,
                "run_id": row.run_id,
                "tool_key": row.tool_key,
                "policy_decision": row.policy_decision,
                "result_status": row.result_status,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        )


class EnterpriseAdminWriteService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象所需的运行时依赖和受限状态。

        Args:
            session: 当前数据库异步会话。
        """
        self._session = session

    async def existing_mutation(
        self, subject: SubjectContext, *, idempotency_key: str, operation: str
    ) -> dict[str, object] | None:
        """Return a prior bounded response for an idempotent admin request.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            idempotency_key: 用于识别重复请求的幂等键。
            operation: 用于执行当前操作的 operation 参数。
        """
        if not idempotency_key or len(idempotency_key) > 128:
            raise GovernanceValidationError("Idempotency key is required and bounded")
        row = await self._session.scalar(
            select(GovernanceAudit).where(
                GovernanceAudit.tenant_id == subject.tenant_id,
                GovernanceAudit.subject_id == subject.user_id,
                GovernanceAudit.resource_type == "admin_mutation",
                GovernanceAudit.resource_id == idempotency_key,
                GovernanceAudit.tool_key == operation,
            )
        )
        return None if row is None else json.loads(row.summary)

    async def save_mutation(
        self, subject: SubjectContext, *, idempotency_key: str, operation: str,
        response: Mapping[str, object],
    ) -> None:
        """Persist one redacted idempotency response in the caller transaction.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            idempotency_key: 用于识别重复请求的幂等键。
            operation: 用于执行当前操作的 operation 参数。
            response: 用于执行当前操作的 response 参数。
        """
        await GovernanceAuditService(self._session).record(
            GovernanceAuditInput(
                tenant_id=subject.tenant_id,
                subject_id=subject.user_id,
                policy_decision="ALLOW",
                result_status="admin_mutation",
                resource_type="admin_mutation",
                resource_id=idempotency_key[:128],
                tool_key=operation[:128],
                summary=json.dumps(dict(response), ensure_ascii=False, sort_keys=True, default=str),
            )
        )

    async def provision_member(
        self,
        subject: SubjectContext,
        *,
        display_name: str,
        login_name: str,
        password: str,
        organization_id: str,
        role: str,
    ) -> dict[str, object]:
        """Provision an employee and an initial non-enterprise membership from server authority.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            display_name: 用于执行当前操作的 display name 参数。
            login_name: 用于执行当前操作的 login name 参数。
            password: 用于执行当前操作的 password 参数。
            organization_id: 用于执行当前操作的 organization id 参数。
            role: 用于执行当前操作的 role 参数。
        """
        require_admin_role(subject, GovernedRole.ENTERPRISE_ADMIN)
        organization = await self._session.get(Organization, organization_id)
        if organization is None or organization.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Member organization is unavailable")
        try:
            governed_role = GovernedRole(role)
        except ValueError as exc:
            raise GovernanceValidationError("Member role is invalid") from exc
        if governed_role is GovernedRole.ENTERPRISE_ADMIN:
            raise GovernanceValidationError("Enterprise administrator provisioning is unavailable")
        user = await LocalAuthenticationService(self._session).provision_member(
            display_name=display_name,
            login_name=login_name,
            password=password,
            organization_id=organization.id,
            role=governed_role,
        )
        return {
            "user_id": user.id,
            "display_name": user.display_name,
            "organization_id": organization.id,
            "role": governed_role.value,
            "status": "active",
        }

    async def assign_member_role(
        self,
        subject: SubjectContext,
        *,
        user_id: str,
        organization_id: str,
        role: str,
    ) -> dict[str, object]:
        """Assign one supported role in one tenant organization without role amplification.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            user_id: 目标用户 ID。
            organization_id: 用于执行当前操作的 organization id 参数。
            role: 用于执行当前操作的 role 参数。
        """
        require_admin_role(subject, GovernedRole.ENTERPRISE_ADMIN)
        user = await self._session.get(User, user_id)
        organization = await self._session.get(Organization, organization_id)
        tenant = await self._session.get(Tenant, subject.tenant_id)
        if (
            user is None
            or organization is None
            or tenant is None
            or user.tenant_id != subject.tenant_id
            or organization.tenant_id != subject.tenant_id
        ):
            raise GovernanceValidationError("Member assignment is unavailable")
        try:
            governed_role = GovernedRole(role)
        except ValueError as exc:
            raise GovernanceValidationError("Member role is invalid") from exc
        if governed_role is GovernedRole.ENTERPRISE_ADMIN:
            raise GovernanceValidationError("Enterprise administrator assignment is unavailable")
        membership = await self._session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.tenant_id == subject.tenant_id,
                OrganizationMembership.organization_id == organization.id,
                OrganizationMembership.user_id == user.id,
            )
        )
        if membership is None:
            membership = OrganizationMembership(
                tenant_id=subject.tenant_id,
                organization_id=organization.id,
                user_id=user.id,
                role=governed_role.value,
                status="active",
            )
            self._session.add(membership)
        else:
            membership.role = governed_role.value
            membership.status = "active"
        tenant.authority_revision += 1
        await self._session.flush()
        return {
            "user_id": user.id,
            "organization_id": organization.id,
            "role": membership.role,
            "status": membership.status,
        }

    async def create_organization(
        self, subject: SubjectContext, *, name: str, organization_type: str = "department",
        parent_id: str | None = None,
    ) -> dict[str, object]:
        """执行 create organization 操作。

        Args:
            subject: 用于执行当前操作的 subject 参数。
            name: 目标对象或能力的名称。
            organization_type: 用于执行当前操作的 organization type 参数。
            parent_id: 用于执行当前操作的 parent id 参数。
        """
        require_admin_role(subject, GovernedRole.ENTERPRISE_ADMIN)
        if not name.strip() or organization_type not in {"root", "department", "team"}:
            raise GovernanceValidationError("Organization input is invalid")
        if parent_id is not None:
            parent = await self._session.get(Organization, parent_id)
            if parent is None or parent.tenant_id != subject.tenant_id:
                raise GovernanceValidationError("Organization parent is unavailable")
        row = Organization(
            tenant_id=subject.tenant_id,
            parent_id=parent_id,
            name=name.strip()[:255],
            type=organization_type,
            status="active",
        )
        self._session.add(row)
        await self._session.flush()
        await set_subject_governance_context(self._session, subject)
        return {"id": row.id, "name": row.name, "type": row.type, "status": row.status}

    async def create_connector(
        self, subject: SubjectContext, *, key: str, connector_type: str,
        display_name: str, config: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """执行 create connector 操作。

        Args:
            subject: 用于执行当前操作的 subject 参数。
            key: 用于执行当前操作的 key 参数。
            connector_type: 用于执行当前操作的 connector type 参数。
            display_name: 用于执行当前操作的 display name 参数。
            config: 用于执行当前操作的 config 参数。
        """
        require_admin_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.CONNECTOR_ADMIN)
        await require_ai_department_administrator(self._session, subject)
        if not key.strip() or connector_type not in {"MCP", "REST", "FEISHU"}:
            raise GovernanceValidationError("Connector input is invalid")
        row = Connector(
            tenant_id=subject.tenant_id, key=key.strip()[:128], type=connector_type,
            display_name=display_name.strip()[:255], config_json=json.dumps(dict(config or {}), sort_keys=True),
            created_by=subject.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        return {"id": row.id, "key": row.key, "type": row.type, "status": row.status}

    async def create_connector_instance(
        self, subject: SubjectContext, *, connector_id: str, organization_id: str,
        auth_mode: str, credential_ref: str | None = None,
    ) -> dict[str, object]:
        """执行 create connector instance 操作。

        Args:
            subject: 用于执行当前操作的 subject 参数。
            connector_id: 用于执行当前操作的 connector id 参数。
            organization_id: 用于执行当前操作的 organization id 参数。
            auth_mode: 用于执行当前操作的 auth mode 参数。
            credential_ref: 用于执行当前操作的 credential ref 参数。
        """
        require_admin_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.CONNECTOR_ADMIN)
        connector = await self._session.get(Connector, connector_id)
        organization = await self._session.get(Organization, organization_id)
        if connector is None or organization is None or connector.tenant_id != subject.tenant_id or organization.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Connector scope is unavailable")
        row = ConnectorInstance(
            tenant_id=subject.tenant_id, connector_id=connector_id, organization_id=organization_id,
            auth_mode=auth_mode[:32], credential_ref=credential_ref, status="draft",
        )
        self._session.add(row)
        await self._session.flush()
        return {"id": row.id, "connector_id": connector_id, "organization_id": organization_id, "status": row.status}

    async def review_connector_tool(
        self, subject: SubjectContext, *, tool_id: str, internal_tool_key: str,
        risk_level: str, enabled: bool,
    ) -> dict[str, object]:
        """执行 review connector tool 操作。

        Args:
            subject: 用于执行当前操作的 subject 参数。
            tool_id: 用于执行当前操作的 tool id 参数。
            internal_tool_key: 用于执行当前操作的 internal tool key 参数。
            risk_level: 用于执行当前操作的 risk level 参数。
            enabled: 是否启用当前能力。
        """
        if enabled:
            await require_ai_department_administrator(self._session, subject)
        else:
            require_admin_role(
                subject,
                GovernedRole.ENTERPRISE_ADMIN,
                GovernedRole.CONNECTOR_ADMIN,
            )
        tool = await self._session.get(ConnectorTool, tool_id)
        if tool is None or tool.tenant_id != subject.tenant_id or risk_level not in {"R0", "R1", "R2", "R3", "R4"}:
            raise GovernanceValidationError("Connector tool review is invalid")
        tool.internal_tool_key = internal_tool_key.strip()[:128]
        tool.risk_level = risk_level
        tool.enabled = enabled
        await self._session.flush()
        return {"id": tool.id, "internal_tool_key": tool.internal_tool_key, "risk_level": tool.risk_level, "enabled": tool.enabled}

    async def distribute_capability(
        self, subject: SubjectContext, *, capability_version_id: str,
        organization_id: str, can_delegate: bool = False,
    ) -> dict[str, object]:
        """执行 distribute capability 操作。

        Args:
            subject: 用于执行当前操作的 subject 参数。
            capability_version_id: 用于执行当前操作的 capability version id 参数。
            organization_id: 用于执行当前操作的 organization id 参数。
            can_delegate: 用于执行当前操作的 can delegate 参数。
        """
        require_admin_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.DEPARTMENT_ADMIN)
        version = await self._session.get(CapabilityVersion, capability_version_id)
        organization = await self._session.get(Organization, organization_id)
        if version is None or organization is None or version.tenant_id != subject.tenant_id or organization.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Capability distribution is unavailable")
        if (
            GovernedRole.ENTERPRISE_ADMIN not in subject.roles
            and organization.id not in subject.organization_ids
        ):
            raise GovernanceValidationError("Department administrator cannot distribute outside its scope")
        row = CapabilityGrant(
            tenant_id=subject.tenant_id,
            source_organization_id=subject.organization_ids[0] if subject.organization_ids else organization_id,
            target_organization_id=organization_id,
            capability_id=version.capability_id,
            version_constraint=version.version,
            can_delegate=can_delegate,
            max_delegate_depth=1 if can_delegate else 0,
            constraints_json="{}",
            status="active",
        )
        self._session.add(row)
        await self._session.flush()
        return {"id": row.id, "capability_version_id": capability_version_id, "organization_id": organization_id, "status": row.status}
