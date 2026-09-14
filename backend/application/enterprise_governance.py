from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.core.registry import ToolInvocation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.policies.enterprise import (
    CapabilityGrantView,
    GovernanceValidationError,
    GovernedAgentProfile,
    GovernedRole,
    LOCAL_ORGANIZATION_ID,
    LOCAL_TENANT_ID,
    PROFILE_SCHEMA_VERSION,
    PolicyDecision,
    PolicyEffect,
    PolicyStatement,
    ResourceContext,
    ResourceScope,
    ResourceVisibility,
    SubjectContext,
    authorize,
    local_subject,
    validate_delegation,
)
from domain.models import (
    CapabilityDefinition,
    CapabilityGrant,
    Organization,
    OrganizationMembership,
    PolicyRule as PersistedPolicyRule,
    Task,
    Tenant,
    User,
)


class EnterpriseGovernanceService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """Bind enterprise governance writes to one database transaction.

        Args:
            session: 当前数据库异步会话。
        """
        self.session = session

    async def ensure_local_foundation(self) -> tuple[Tenant, Organization]:
        """执行当前组件定义的业务处理逻辑。"""
        tenant = await self.session.get(Tenant, LOCAL_TENANT_ID)
        if tenant is None:
            tenant = Tenant(
                id=LOCAL_TENANT_ID,
                name="Local",
                status="active",
                authority_revision=0,
            )
            self.session.add(tenant)
            await self.session.flush()
        organization = await self.session.get(Organization, LOCAL_ORGANIZATION_ID)
        if organization is None:
            organization = Organization(
                id=LOCAL_ORGANIZATION_ID,
                tenant_id=tenant.id,
                parent_id=None,
                name="Local",
                type="enterprise",
                status="active",
            )
            self.session.add(organization)
            await self.session.flush()
        return tenant, organization

    async def add_membership(
        self,
        *,
        tenant_id: str,
        organization_id: str,
        user_id: str,
        role: GovernedRole,
    ) -> OrganizationMembership:
        """Add a supported same-tenant membership and increment authority revision.

        Args:
            tenant_id: 用于执行当前操作的 tenant id 参数。
            organization_id: 用于执行当前操作的 organization id 参数。
            user_id: 目标用户 ID。
            role: 用于执行当前操作的 role 参数。
        """
        tenant = await self.session.get(Tenant, tenant_id)
        organization = await self.session.get(Organization, organization_id)
        user = await self.session.get(User, user_id)
        if tenant is None or organization is None or user is None:
            raise GovernanceValidationError("Membership subject is unavailable")
        if organization.tenant_id != tenant_id or user.tenant_id != tenant_id:
            raise GovernanceValidationError("Membership cannot cross tenants")
        membership = OrganizationMembership(
            tenant_id=tenant_id,
            organization_id=organization_id,
            user_id=user_id,
            role=role.value,
            status="active",
        )
        self.session.add(membership)
        tenant.authority_revision += 1
        await self.session.flush()
        return membership

    async def create_organization(
        self,
        *,
        tenant_id: str,
        name: str,
        organization_type: str = "department",
        parent_id: str | None = None,
    ) -> Organization:
        """Create an organization only beneath a parent in the same tenant.

        Args:
            tenant_id: 用于执行当前操作的 tenant id 参数。
            name: 目标对象或能力的名称。
            organization_type: 用于执行当前操作的 organization type 参数。
            parent_id: 用于执行当前操作的 parent id 参数。
        """
        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None or not name.strip():
            raise GovernanceValidationError("Organization tenant and name are required")
        if parent_id is not None:
            parent = await self.session.get(Organization, parent_id)
            if parent is None or parent.tenant_id != tenant_id:
                raise GovernanceValidationError("Organization parent cannot cross tenants")
        organization = Organization(
            tenant_id=tenant_id,
            parent_id=parent_id,
            name=name.strip()[:255],
            type=organization_type.strip()[:32] or "department",
            status="active",
        )
        self.session.add(organization)
        tenant.authority_revision += 1
        await self.session.flush()
        return organization

    async def create_grant(
        self,
        *,
        tenant_id: str,
        source_organization_id: str,
        target_organization_id: str,
        capability_id: str,
        version_constraint: str,
        can_delegate: bool = False,
        max_delegate_depth: int = 0,
        constraints: dict[str, object] | None = None,
        expires_at: datetime | None = None,
        bootstrap: bool = False,
    ) -> CapabilityGrant:
        """Create a root or validated delegated Capability grant without amplification.

        Args:
            tenant_id: 用于执行当前操作的 tenant id 参数。
            source_organization_id: 用于执行当前操作的 source organization id 参数。
            target_organization_id: 用于执行当前操作的 target organization id 参数。
            capability_id: 用于执行当前操作的 capability id 参数。
            version_constraint: 用于执行当前操作的 version constraint 参数。
            can_delegate: 用于执行当前操作的 can delegate 参数。
            max_delegate_depth: 用于执行当前操作的 max delegate depth 参数。
            constraints: 用于执行当前操作的 constraints 参数。
            expires_at: 用于执行当前操作的 expires at 参数。
            bootstrap: 用于执行当前操作的 bootstrap 参数。
        """
        tenant = await self.session.get(Tenant, tenant_id)
        source = await self.session.get(Organization, source_organization_id)
        target = await self.session.get(Organization, target_organization_id)
        capability = await self.session.get(CapabilityDefinition, capability_id)
        if tenant is None or source is None or target is None or capability is None:
            raise GovernanceValidationError("Capability grant subject is unavailable")
        if {source.tenant_id, target.tenant_id, capability.tenant_id} != {tenant_id}:
            raise GovernanceValidationError("Capability grant cannot cross tenants")
        child = CapabilityGrantView(
            tenant_id=tenant_id,
            source_organization_id=source_organization_id,
            target_organization_id=target_organization_id,
            capability_key=capability.key,
            version=version_constraint,
            can_delegate=can_delegate,
            max_delegate_depth=max_delegate_depth,
            constraints=constraints or {},
            expires_at=expires_at,
        )
        if not bootstrap:
            parent_record = await self.session.scalar(
                select(CapabilityGrant)
                .where(
                    CapabilityGrant.tenant_id == tenant_id,
                    CapabilityGrant.target_organization_id == source_organization_id,
                    CapabilityGrant.capability_id == capability_id,
                    CapabilityGrant.status == "active",
                )
                .order_by(CapabilityGrant.max_delegate_depth.desc())
                .limit(1)
            )
            if parent_record is None:
                raise GovernanceValidationError("Delegation source lacks the capability")
            parent = _grant_view(parent_record, capability)
            validate_delegation(parent, child)
        elif source.parent_id is not None or (
            target.id != source.id and target.parent_id != source.id
        ):
            raise GovernanceValidationError(
                "Bootstrap grants are limited to a tenant root and its direct children"
            )
        record = CapabilityGrant(
            tenant_id=tenant_id,
            source_organization_id=source_organization_id,
            target_organization_id=target_organization_id,
            capability_id=capability_id,
            version_constraint=version_constraint,
            can_delegate=can_delegate,
            max_delegate_depth=max_delegate_depth,
            constraints_json=_safe_json(constraints or {}),
            expires_at=expires_at,
            status="active",
        )
        self.session.add(record)
        tenant.authority_revision += 1
        await self.session.flush()
        return record

    async def create_policy_rule(
        self,
        *,
        tenant_id: str,
        scope_type: str,
        scope_id: str | None,
        action: str,
        resource_type: str,
        effect: PolicyEffect,
        constraints: dict[str, dict[str, object]] | None = None,
        priority: int = 0,
    ) -> PersistedPolicyRule:
        """Persist one bounded policy rule and advance the authority revision.

        Args:
            tenant_id: 用于执行当前操作的 tenant id 参数。
            scope_type: 用于执行当前操作的 scope type 参数。
            scope_id: 用于执行当前操作的 scope id 参数。
            action: 用于执行当前操作的 action 参数。
            resource_type: 用于执行当前操作的 resource type 参数。
            effect: 用于执行当前操作的 effect 参数。
            constraints: 用于执行当前操作的 constraints 参数。
            priority: 用于执行当前操作的 priority 参数。
        """
        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None:
            raise GovernanceValidationError("Policy tenant is unavailable")
        if scope_type not in {"enterprise", "organization", "role", "user"}:
            raise GovernanceValidationError("Policy scope is unsupported")
        if not action.strip() or not resource_type.strip():
            raise GovernanceValidationError("Policy action and resource are required")
        record = PersistedPolicyRule(
            tenant_id=tenant_id,
            scope_type=scope_type,
            scope_id=scope_id,
            action=action,
            resource_type=resource_type,
            effect=effect.value,
            constraints_json=_safe_json(constraints or {}),
            priority=priority,
            status="active",
        )
        self.session.add(record)
        tenant.authority_revision += 1
        await self.session.flush()
        return record


class GovernedAgentProfileResolver:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """Bind profile resolution to current database facts in one transaction.

        Args:
            session: 当前数据库异步会话。
        """
        self.session = session

    async def resolve_for_task(self, task: Task) -> GovernedAgentProfile:
        """Resolve and store the current governed profile before Agent execution.

        Args:
            task: 需要处理的任务对象。
        """
        user = await self.session.get(User, task.user_id)
        if user is None or user.tenant_id != task.tenant_id:
            raise GovernanceValidationError("Task identity is inconsistent")
        tenant = await self.session.get(Tenant, task.tenant_id)
        if tenant is None:
            if task.tenant_id != LOCAL_TENANT_ID:
                raise GovernanceValidationError("Task tenant is unavailable")
            tenant, _ = await EnterpriseGovernanceService(
                self.session
            ).ensure_local_foundation()
        memberships = tuple(
            await self.session.scalars(
                select(OrganizationMembership).where(
                    OrganizationMembership.tenant_id == task.tenant_id,
                    OrganizationMembership.user_id == task.user_id,
                    OrganizationMembership.status == "active",
                )
            )
        )
        organization_ids = tuple(
            dict.fromkeys(
                membership.organization_id for membership in memberships
            )
        )
        roles = tuple(
            dict.fromkeys(GovernedRole(membership.role) for membership in memberships)
        )
        if not organization_ids and task.tenant_id == LOCAL_TENANT_ID:
            organization_ids = (task.organization_id or LOCAL_ORGANIZATION_ID,)
            roles = (GovernedRole.MEMBER,)
        subject = SubjectContext(
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            organization_ids=organization_ids,
            roles=roles,
            authority_revision=tenant.authority_revision,
        )
        grants = tuple(
            await self.session.scalars(
                select(CapabilityGrant).where(
                    CapabilityGrant.tenant_id == task.tenant_id,
                    CapabilityGrant.target_organization_id.in_(organization_ids),
                    CapabilityGrant.status == "active",
                )
            )
        ) if organization_ids else ()
        policy_records = tuple(
            await self.session.scalars(
                select(PersistedPolicyRule).where(
                    PersistedPolicyRule.tenant_id == task.tenant_id,
                    PersistedPolicyRule.status == "active",
                )
            )
        )
        capabilities: list[str] = []
        skills: list[str] = []
        tools: list[str] = []
        knowledge_scopes: list[str] = []
        now = datetime.now(UTC)
        try:
            requested_skills = tuple(json.loads(task.requested_skill_names_json or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            requested_skills = ()
        for grant in grants:
            expiry = grant.expires_at
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry is not None and expiry <= now:
                continue
            definition = await self.session.get(CapabilityDefinition, grant.capability_id)
            if definition is None or definition.status != "active":
                continue
            if _capability_denied(
                subject=subject,
                task=task,
                capability_key=definition.key,
                records=policy_records,
            ):
                continue
            bindings = _json_object(definition.bindings_json)
            capabilities.append(f"{definition.key}@{grant.version_constraint}")
            skills.extend(_string_list(bindings.get("skills")))
            tools.extend(_string_list(bindings.get("tools")))
            knowledge_scopes.extend(_string_list(bindings.get("knowledge_scopes")))
        authorized_skills = tuple(sorted(set(skills)))
        selected_skills = tuple(item for item in requested_skills if item in authorized_skills)
        profile = GovernedAgentProfile(
            subject=subject,
            capabilities=tuple(sorted(set(capabilities))),
            skills=tuple(dict.fromkeys((*selected_skills, *authorized_skills))),
            tools=tuple(sorted(set(tools))),
            knowledge_scopes=tuple(sorted(set(knowledge_scopes))),
        )
        task.agent_profile_schema_version = PROFILE_SCHEMA_VERSION
        task.agent_profile_snapshot = profile.to_snapshot()
        await self.session.flush()
        return profile

    async def resolve_for_user(self, user_id: str) -> GovernedAgentProfile:
        """Resolve a fresh read-only profile without creating a product task.

        Args:
            user_id: 目标用户 ID。
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise GovernanceValidationError("Local user is unavailable")
        transient = Task(
            id=f"profile:{user_id}",
            user_id=user.id,
            tenant_id=user.tenant_id,
            organization_id=None,
            owner_type="user",
            owner_id=user.id,
            visibility=ResourceVisibility.PRIVATE.value,
            platform="profile",
            task_type="profile",
            input_text="",
            status="pending",
        )
        return await self.resolve_for_task(transient)


class SqlAlchemyToolPolicyAuthorizer:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """Bind execution-time authorization to the current transaction.

        Args:
            session: 当前数据库异步会话。
        """
        self.session = session

    async def authorize(
        self, invocation: ToolInvocation, *, risk_level: str
    ) -> PolicyDecision:
        """Check task owner, profile tools, current rules, and risk defaults.

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
            risk_level: 用于执行当前操作的 risk level 参数。
        """
        task = await self.session.get(Task, invocation.task_id)
        if task is None or task.user_id != invocation.user_id:
            return PolicyDecision(PolicyEffect.DENY, "task_owner_mismatch")
        snapshot = task.agent_profile_snapshot
        if not snapshot:
            return PolicyDecision(PolicyEffect.ALLOW, "legacy_profile_adapter")
        try:
            profile = GovernedAgentProfile.from_snapshot(snapshot)
        except GovernanceValidationError:
            return PolicyDecision(PolicyEffect.DENY, "profile_snapshot_invalid")
        if profile.subject.authority_revision != await self._authority_revision(
            task.tenant_id
        ):
            return PolicyDecision(PolicyEffect.DENY, "authority_revision_stale")
        if not profile.capabilities and task.tenant_id == LOCAL_TENANT_ID:
            return PolicyDecision(PolicyEffect.ALLOW, "legacy_profile_adapter")
        if not profile.capabilities:
            return PolicyDecision(PolicyEffect.DENY, "no_effective_capability")
        if profile.capabilities and invocation.name not in profile.tools:
            return PolicyDecision(PolicyEffect.DENY, "tool_not_in_effective_capability")
        records = tuple(
            await self.session.scalars(
                select(PersistedPolicyRule).where(
                    PersistedPolicyRule.tenant_id == task.tenant_id,
                    PersistedPolicyRule.status == "active",
                    PersistedPolicyRule.action.in_(
                        ("*", "tool.execute", f"tool.execute:{invocation.name}")
                    ),
                )
            )
        )
        return authorize(
            subject=profile.subject,
            action=f"tool.execute:{invocation.name}",
            resource=ResourceContext(
                resource_type="tool",
                resource_id=invocation.name,
                scope=ResourceScope(
                    tenant_id=task.tenant_id,
                    organization_id=task.organization_id,
                    owner_type=task.owner_type,
                    owner_id=task.owner_id or task.user_id,
                    visibility=ResourceVisibility(task.visibility),
                ),
            ),
            context=invocation.arguments,
            statements=tuple(_policy_statement(record) for record in records),
            risk=risk_level,
        )

    async def _authority_revision(self, tenant_id: str) -> int:
        """Read the current revision so stale snapshots never preserve grants.

        Args:
            tenant_id: 用于执行当前操作的 tenant id 参数。
        """
        tenant = await self.session.get(Tenant, tenant_id)
        return tenant.authority_revision if tenant is not None else -1


async def resolve_local_subject(session: AsyncSession, user_id: str) -> SubjectContext:
    """Resolve local server-side memberships without trusting client claims.

    Args:
        session: 当前数据库异步会话。
        user_id: 目标用户 ID。
    """
    user = await session.get(User, user_id)
    if user is None:
        raise GovernanceValidationError("Local user is unavailable")
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None and user.tenant_id == LOCAL_TENANT_ID:
        tenant, _ = await EnterpriseGovernanceService(session).ensure_local_foundation()
    if tenant is None:
        raise GovernanceValidationError("Local tenant is unavailable")
    memberships = tuple(
        await session.scalars(
            select(OrganizationMembership).where(
                OrganizationMembership.tenant_id == user.tenant_id,
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.status == "active",
            )
        )
    )
    if not memberships and user.tenant_id == LOCAL_TENANT_ID:
        return local_subject(user_id, authority_revision=tenant.authority_revision)
    return SubjectContext(
        tenant_id=user.tenant_id,
        user_id=user_id,
        organization_ids=tuple(item.organization_id for item in memberships),
        roles=tuple(GovernedRole(item.role) for item in memberships),
        authority_revision=tenant.authority_revision,
    )


def _grant_view(
    grant: CapabilityGrant, capability: CapabilityDefinition
) -> CapabilityGrantView:
    """Convert persisted grant data into the closed delegation contract.

    Args:
        grant: 用于执行当前操作的 grant 参数。
        capability: 用于执行当前操作的 capability 参数。
    """
    return CapabilityGrantView(
        tenant_id=grant.tenant_id,
        source_organization_id=grant.source_organization_id,
        target_organization_id=grant.target_organization_id,
        capability_key=capability.key,
        version=grant.version_constraint,
        can_delegate=grant.can_delegate,
        max_delegate_depth=grant.max_delegate_depth,
        constraints=_json_object(grant.constraints_json),
        expires_at=grant.expires_at,
        status=grant.status,
    )


def _policy_statement(record: PersistedPolicyRule) -> PolicyStatement:
    """Convert persisted safe policy JSON into the pure evaluator contract.

    Args:
        record: 用于执行当前操作的 record 参数。
    """
    return PolicyStatement(
        effect=PolicyEffect(record.effect),
        action=(
            "*"
            if record.action in {"tool.execute", "capability.use"}
            else record.action
        ),
        scope_type=record.scope_type,
        scope_id=record.scope_id,
        resource_type=record.resource_type,
        constraints=_json_object(record.constraints_json),
        priority=record.priority,
    )


def _capability_denied(
    *,
    subject: SubjectContext,
    task: Task,
    capability_key: str,
    records: tuple[PersistedPolicyRule, ...],
) -> bool:
    """Exclude granted Capability candidates when current applicable policy denies.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        task: 需要处理的任务对象。
        capability_key: 用于执行当前操作的 capability key 参数。
        records: 用于执行当前操作的 records 参数。
    """
    action = f"capability.use:{capability_key}"
    matching = tuple(
        record for record in records if record.action in {"*", "capability.use", action}
    )
    if not matching:
        return False
    decision = authorize(
        subject=subject,
        action=action,
        resource=ResourceContext(
            resource_type="capability",
            resource_id=capability_key,
            scope=ResourceScope(
                tenant_id=task.tenant_id,
                organization_id=task.organization_id,
                owner_type=task.owner_type,
                owner_id=task.owner_id or task.user_id,
                visibility=ResourceVisibility(task.visibility),
            ),
        ),
        statements=tuple(_policy_statement(record) for record in matching),
    )
    return decision.effect is PolicyEffect.DENY


def _safe_json(value: object) -> str:
    """Serialize bounded governance metadata deterministically.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > 16_000:
        raise GovernanceValidationError("Governance metadata is too large")
    return encoded


def _json_object(value: str) -> dict[str, Any]:
    """Parse a persisted JSON object and fail closed for malformed data.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise GovernanceValidationError("Governance metadata is invalid") from exc
    if not isinstance(parsed, dict):
        raise GovernanceValidationError("Governance metadata must be an object")
    return parsed


def _string_list(value: object) -> tuple[str, ...]:
    """Return bounded strings from an internal Capability binding list.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    if not isinstance(value, list) or len(value) > 100:
        return ()
    return tuple(str(item)[:128] for item in value if isinstance(item, str) and item)


__all__ = [
    "EnterpriseGovernanceService",
    "GovernedAgentProfileResolver",
    "SqlAlchemyToolPolicyAuthorizer",
    "resolve_local_subject",
]
