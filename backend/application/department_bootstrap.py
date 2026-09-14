from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.capabilities import CapabilityNotFoundError, CapabilityRegistry
from application.enterprise_governance import EnterpriseGovernanceService
from application.enterprise_runtime import require_admin_role
from domain.models import (
    CapabilityDefinition,
    CapabilityGrant,
    CapabilityVersion,
    Organization,
    OrganizationMembership,
    Tenant,
)
from domain.policies.enterprise import (
    GovernedRole,
    GovernanceValidationError,
    LOCAL_ORGANIZATION_ID,
    SubjectContext,
)


CORE_CAPABILITY_KEYS: Final[tuple[str, ...]] = (
    "code.memory",
    "code.status",
    "profile.plan",
    "profile.learn",
    "profile.daily",
    "profile.office",
    "capability.ai.improvement-operations",
    "capability.finance.office-productivity",
    "capability.market.content-productivity",
)
CORE_CAPABILITY_VERSION: Final[str] = "builtin.v1"
DEMO_CAPABILITY_SKILLS: Final[dict[str, tuple[str, ...]]] = {
    "capability.ai.improvement-operations": (
        "skill-creator",
        "skill-installer",
        "define-goal",
        "security-best-practices",
        "security-threat-model",
        "security-ownership-map",
        "jupyter-notebook",
    ),
    "capability.finance.office-productivity": (
        "office-writing",
        "meeting-minutes",
        "business-email",
        "progress-report",
        "proposal-writing",
        "presentation-briefing",
        "structured-spreadsheet",
    ),
    "capability.market.content-productivity": (
        "pdf",
        "speech",
        "transcribe",
        "notion-meeting-intelligence",
        "notion-knowledge-capture",
        "figma",
        "presentation-briefing",
    ),
}
AI_DEPARTMENT_NAME: Final[str] = "AI部"


@dataclass(frozen=True)
class DepartmentTemplate:
    """定义当前组件的职责和边界。"""

    name: str  # name 对应的数据字段。
    capability_keys: tuple[str, ...]  # capability_keys 对应的数据字段。


DEPARTMENT_TEMPLATES: Final[tuple[DepartmentTemplate, ...]] = (
    DepartmentTemplate(
        AI_DEPARTMENT_NAME,
        (
            "profile.plan", "code.status", "profile.learn", "profile.office",
            "code.memory", "capability.ai.improvement-operations",
        ),
    ),
    DepartmentTemplate(
        "财务部",
        (
            "profile.plan", "code.status", "profile.office", "code.memory",
            "profile.learn", "capability.finance.office-productivity",
        ),
    ),
    DepartmentTemplate(
        "市场部",
        (
            "profile.plan", "code.status", "profile.learn", "profile.office",
            "code.memory", "capability.market.content-productivity",
        ),
    ),
)


class DepartmentBootstrapService:
    """定义当前组件的职责和边界。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        capability_registry: CapabilityRegistry,
    ) -> None:
        """Bind one bootstrap operation to database state and the live catalog.

        Args:
            session: 当前数据库异步会话。
            capability_registry: 用于执行当前操作的 capability registry 参数。
        """
        self.session = session
        self.capability_registry = capability_registry

    async def bootstrap(self, subject: SubjectContext) -> dict[str, object]:
        """Idempotently establish AI, finance, and technology without creating IT.

        Only an enterprise administrator may create organization records or
        department grants. Repeated calls reuse the existing active records and
        do not amplify delegation or create duplicates.

        Args:
            subject: 用于执行当前操作的 subject 参数。
        """
        require_admin_role(subject, GovernedRole.ENTERPRISE_ADMIN)
        root = await self.session.get(Organization, LOCAL_ORGANIZATION_ID)
        if root is None or root.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Department root is unavailable")

        await self._ensure_registered_core_capabilities(subject)
        departments: list[dict[str, object]] = []
        for template in DEPARTMENT_TEMPLATES:
            organization = await self._organization(subject, root, template.name)
            if template.name == AI_DEPARTMENT_NAME:
                await self._ensure_ai_admin_membership(subject, organization)
            capability_keys = []
            for key in template.capability_keys:
                definition = await self._definition(subject, key)
                await self._grant(subject, root, organization, definition)
                capability_keys.append(definition.key)
            departments.append(
                {
                    "id": organization.id,
                    "name": organization.name,
                    "type": organization.type,
                    "status": organization.status,
                    "capability_keys": capability_keys,
                }
            )
        return {"departments": departments, "it_department_created": False}

    async def _ensure_ai_admin_membership(
        self,
        subject: SubjectContext,
        organization: Organization,
    ) -> OrganizationMembership:
        """Make the acting enterprise administrator an idempotent AI department lead.

        Args:
            subject: Server-resolved enterprise administrator.
            organization: Active AI department created by this bootstrap.
        """
        membership = await self.session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.tenant_id == subject.tenant_id,
                OrganizationMembership.organization_id == organization.id,
                OrganizationMembership.user_id == subject.user_id,
                OrganizationMembership.role == GovernedRole.DEPARTMENT_ADMIN.value,
            )
        )
        if membership is not None:
            if membership.status != "active":
                membership.status = "active"
                tenant = await self.session.get(Tenant, subject.tenant_id)
                if tenant is None:
                    raise GovernanceValidationError("Capability tenant is unavailable")
                tenant.authority_revision += 1
                await self.session.flush()
            return membership
        return await EnterpriseGovernanceService(self.session).add_membership(
            tenant_id=subject.tenant_id,
            organization_id=organization.id,
            user_id=subject.user_id,
            role=GovernedRole.DEPARTMENT_ADMIN,
        )

    async def _ensure_registered_core_capabilities(
        self, subject: SubjectContext
    ) -> None:
        """Synchronize the six enabled built-ins into tenant governance records.

        Args:
            subject: 用于执行当前操作的 subject 参数。
        """
        for key in CORE_CAPABILITY_KEYS:
            definition = await self._definition(subject, key)
            await self._ensure_approved_version(subject, definition)

    async def _organization(
        self, subject: SubjectContext, root: Organization, name: str
    ) -> Organization:
        """Return one active direct-child department without changing unrelated orgs.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            root: 用于执行当前操作的 root 参数。
            name: 目标对象或能力的名称。
        """
        organization = await self.session.scalar(
            select(Organization).where(
                Organization.tenant_id == subject.tenant_id,
                Organization.parent_id == root.id,
                Organization.name == name,
                Organization.type == "department",
            )
        )
        if organization is not None:
            if organization.status != "active":
                organization.status = "active"
            return organization
        return await EnterpriseGovernanceService(self.session).create_organization(
            tenant_id=subject.tenant_id,
            parent_id=root.id,
            name=name,
            organization_type="department",
        )

    async def _definition(self, subject: SubjectContext, key: str) -> CapabilityDefinition:
        """Resolve an active DB definition backed by an enabled registry capability.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            key: 用于执行当前操作的 key 参数。
        """
        try:
            metadata = self.capability_registry.get(key)
        except CapabilityNotFoundError as exc:
            raise GovernanceValidationError(
                f"Required capability is unavailable: {key}"
            ) from exc
        if not metadata.enabled:
            raise GovernanceValidationError(f"Required capability is disabled: {key}")

        definition = await self.session.scalar(
            select(CapabilityDefinition)
            .where(
                CapabilityDefinition.tenant_id == subject.tenant_id,
                CapabilityDefinition.key == key,
                CapabilityDefinition.status == "active",
            )
            .order_by(CapabilityDefinition.version.desc())
        )
        if definition is not None:
            expected_bindings = self._bindings(metadata)
            if (
                definition.display_name != metadata.display_name
                or (definition.summary or "") != metadata.summary
                or self._bindings_object(definition.bindings_json) != expected_bindings
            ):
                definition.display_name = metadata.display_name
                definition.summary = metadata.summary
                definition.bindings_json = json.dumps(expected_bindings, sort_keys=True)
                tenant = await self.session.get(Tenant, subject.tenant_id)
                if tenant is None:
                    raise GovernanceValidationError("Capability tenant is unavailable")
                tenant.authority_revision += 1
                await self.session.flush()
            return definition

        tenant = await self.session.get(Tenant, subject.tenant_id)
        if tenant is None:
            raise GovernanceValidationError("Capability tenant is unavailable")
        definition = CapabilityDefinition(
            tenant_id=subject.tenant_id,
            key=metadata.id,
            version=CORE_CAPABILITY_VERSION,
            display_name=metadata.display_name,
            summary=metadata.summary,
            bindings_json=json.dumps(self._bindings(metadata), sort_keys=True),
            status="active",
        )
        self.session.add(definition)
        tenant.authority_revision += 1
        await self.session.flush()
        return definition

    @staticmethod
    def _bindings(metadata: object) -> dict[str, object]:
        """Build the canonical bindings payload for one registry capability.

        Args:
            metadata: 用于执行当前操作的 capability metadata 参数。
        """
        bindings: dict[str, object] = {
            "catalog_source": metadata.source,
            "requires_approval": metadata.requires_approval,
            "risk_level": metadata.risk_level,
        }
        approved_skills = DEMO_CAPABILITY_SKILLS.get(metadata.id, ())
        if approved_skills:
            bindings["skills"] = list(approved_skills)
        if metadata.tools:
            bindings["tools"] = list(metadata.tools)
        return bindings

    @staticmethod
    def _bindings_object(raw: str | None) -> dict[str, object]:
        """Parse stored bindings JSON into a comparable dict.

        Args:
            raw: 用于执行当前操作的 bindings JSON 参数。
        """
        try:
            parsed = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def _ensure_approved_version(
        self, subject: SubjectContext, definition: CapabilityDefinition
    ) -> CapabilityVersion:
        """Persist an immutable catalog version so later admin distribution is explicit.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            definition: 用于执行当前操作的 definition 参数。
        """
        version = await self.session.scalar(
            select(CapabilityVersion).where(
                CapabilityVersion.tenant_id == subject.tenant_id,
                CapabilityVersion.capability_id == definition.id,
                CapabilityVersion.version == definition.version,
            )
        )
        if version is not None:
            return version
        composition: dict[str, object] = {
            "capability_key": definition.key,
            "source": "builtin",
        }
        approved_skills = DEMO_CAPABILITY_SKILLS.get(definition.key, ())
        if approved_skills:
            composition["skills"] = list(approved_skills)
        bound_tools = self._bindings_object(definition.bindings_json).get("tools")
        if isinstance(bound_tools, list) and bound_tools:
            composition["tools"] = bound_tools
        version = CapabilityVersion(
            tenant_id=subject.tenant_id,
            capability_id=definition.id,
            version=definition.version,
            composition_json=json.dumps(composition, sort_keys=True),
            published_by=subject.user_id,
        )
        self.session.add(version)
        await self.session.flush()
        return version

    async def _grant(
        self,
        subject: SubjectContext,
        root: Organization,
        organization: Organization,
        definition: CapabilityDefinition,
    ) -> CapabilityGrant:
        """Create a non-delegable root-to-department grant only when absent.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            root: 用于执行当前操作的 root 参数。
            organization: 用于执行当前操作的 organization 参数。
            definition: 用于执行当前操作的 definition 参数。
        """
        grant = await self.session.scalar(
            select(CapabilityGrant).where(
                CapabilityGrant.tenant_id == subject.tenant_id,
                CapabilityGrant.target_organization_id == organization.id,
                CapabilityGrant.capability_id == definition.id,
                CapabilityGrant.version_constraint == definition.version,
                CapabilityGrant.status == "active",
            )
        )
        if grant is not None:
            return grant
        return await EnterpriseGovernanceService(self.session).create_grant(
            tenant_id=subject.tenant_id,
            source_organization_id=root.id,
            target_organization_id=organization.id,
            capability_id=definition.id,
            version_constraint=definition.version,
            can_delegate=False,
            max_delegate_depth=0,
            constraints={},
            bootstrap=True,
        )
