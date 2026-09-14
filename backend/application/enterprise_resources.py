from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Protocol, Sequence, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import (
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    ConnectorInstance,
    ConnectorTool,
    Credential,
    Organization,
    SkillDefinition,
    SkillDraft,
    SkillVersion,
)
from domain.policies.enterprise import GovernanceValidationError, GovernedRole, SubjectContext
from domain.policies.redaction import sanitize_text


_KEY = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_PUBLISH_ROLES = {GovernedRole.CAPABILITY_PUBLISHER, GovernedRole.ENTERPRISE_ADMIN}
_CONNECTOR_ROLES = {GovernedRole.CONNECTOR_ADMIN, GovernedRole.ENTERPRISE_ADMIN}
_DISTRIBUTION_ROLES = {GovernedRole.DEPARTMENT_ADMIN, GovernedRole.ENTERPRISE_ADMIN}


class CredentialCipher(Protocol):
    """定义当前组件的接口契约。"""

    def encrypt(self, plaintext: str) -> str:
        """Return ciphertext suitable for persistence.

        Args:
            plaintext: 用于执行当前操作的 plaintext 参数。
        """

    def decrypt(self, ciphertext: str) -> str:
        """Return plaintext only for an authorized execution path.

        Args:
            ciphertext: 用于执行当前操作的 ciphertext 参数。
        """


class ConnectorDiscoveryClient(Protocol):
    """定义当前组件的接口契约。"""

    async def test_connection(self) -> None:
        """执行当前组件定义的业务处理逻辑。"""

    async def list_tools(self) -> Sequence[Mapping[str, Any]]:
        """执行当前组件定义的业务处理逻辑。"""


def _require_role(subject: SubjectContext, roles: set[GovernedRole]) -> None:
    """Reject management operations unless trusted server roles authorize them.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        roles: 用于执行当前操作的 roles 参数。
    """
    if not roles.intersection(subject.roles):
        raise GovernanceValidationError("Subject lacks required management role")


def _canonical(value: object) -> str:
    """Serialize bounded governance data deterministically.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text.encode("utf-8")) > 64_000:
        raise GovernanceValidationError("Governance payload exceeds size limit")
    return text


def _digest(text: str) -> str:
    """Return the immutable content identity used by tests and publication.

    Args:
        text: 需要处理的文本内容。
    """
    return sha256(text.encode("utf-8")).hexdigest()


def validate_skill_content(content: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a declarative Skill without importing any code.

    Args:
        content: 用于执行当前操作的 content 参数。
    """
    allowed = {"skill_md", "tools", "knowledge", "risk", "execution"}
    if set(content) - allowed:
        raise GovernanceValidationError("Skill contains unsupported or executable fields")
    text = content.get("skill_md")
    execution = content.get("execution", {"mode": "server"})
    if not isinstance(text, str) or not text.strip() or len(text) > 40_000:
        raise GovernanceValidationError("Skill documentation is required and bounded")
    if not isinstance(execution, Mapping) or execution.get("mode") != "server":
        raise GovernanceValidationError("Skill execution mode is unsupported")
    lowered = text.lower()
    if any(marker in lowered for marker in ("```python", "```bash", "<script", "entrypoint:")):
        raise GovernanceValidationError("Skill executable content is forbidden")
    tools = content.get("tools", [])
    knowledge = content.get("knowledge", [])
    if not isinstance(tools, list) or not isinstance(knowledge, list):
        raise GovernanceValidationError("Skill dependencies must be lists")
    return {
        "execution": {"mode": "server"},
        "knowledge": sorted({str(item) for item in knowledge}),
        "risk": str(content.get("risk", "R1")),
        "skill_md": text.strip(),
        "tools": sorted({str(item) for item in tools}),
    }


class SkillCapabilityService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """Bind lifecycle operations to one database transaction.

        Args:
            session: 当前数据库异步会话。
        """
        self.session = session

    async def save_draft(
        self,
        *,
        subject: SubjectContext,
        key: str,
        display_name: str,
        content: Mapping[str, Any],
    ) -> SkillDraft:
        """Create or replace the authorized tenant draft with normalized content.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            key: 用于执行当前操作的 key 参数。
            display_name: 用于执行当前操作的 display name 参数。
            content: 用于执行当前操作的 content 参数。
        """
        _require_role(subject, _PUBLISH_ROLES)
        if not _KEY.fullmatch(key):
            raise GovernanceValidationError("Skill key is invalid")
        normalized = validate_skill_content(content)
        canonical = _canonical(normalized)
        skill = await self.session.scalar(select(SkillDefinition).where(SkillDefinition.tenant_id == subject.tenant_id, SkillDefinition.key == key))
        if skill is None:
            skill = SkillDefinition(tenant_id=subject.tenant_id, key=key, display_name=display_name.strip()[:255], status="active")
            self.session.add(skill)
            await self.session.flush()
        draft = await self.session.scalar(select(SkillDraft).where(SkillDraft.skill_id == skill.id))
        if draft is None:
            draft = SkillDraft(tenant_id=subject.tenant_id, skill_id=skill.id, content_json=canonical, content_digest=_digest(canonical), updated_by=subject.user_id)
            self.session.add(draft)
        else:
            if draft.tenant_id != subject.tenant_id:
                raise GovernanceValidationError("Skill draft cannot cross tenants")
            draft.content_json = canonical
            draft.content_digest = _digest(canonical)
            draft.test_status = "pending"
            draft.tested_digest = None
            draft.updated_by = subject.user_id
        await self.session.flush()
        return draft

    async def test_draft(self, *, subject: SubjectContext, skill_id: str) -> SkillDraft:
        """Run deterministic side-effect-free validation for the current draft.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            skill_id: 用于执行当前操作的 skill id 参数。
        """
        _require_role(subject, _PUBLISH_ROLES)
        draft = await self.session.scalar(select(SkillDraft).where(SkillDraft.skill_id == skill_id, SkillDraft.tenant_id == subject.tenant_id))
        if draft is None:
            raise GovernanceValidationError("Skill draft is unavailable")
        validate_skill_content(json.loads(draft.content_json))
        draft.test_status = "passed"
        draft.tested_digest = draft.content_digest
        await self.session.flush()
        return draft

    async def publish_skill(self, *, subject: SubjectContext, skill_id: str, version: str) -> SkillVersion:
        """Publish exactly the tested draft digest as a new immutable fixed version.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            skill_id: 用于执行当前操作的 skill id 参数。
            version: 用于执行当前操作的 version 参数。
        """
        _require_role(subject, _PUBLISH_ROLES)
        if not _VERSION.fullmatch(version):
            raise GovernanceValidationError("Skill version must be semantic and fixed")
        draft = await self.session.scalar(select(SkillDraft).where(SkillDraft.skill_id == skill_id, SkillDraft.tenant_id == subject.tenant_id))
        if draft is None or draft.test_status != "passed" or draft.tested_digest != draft.content_digest:
            raise GovernanceValidationError("Skill draft must pass tests before publication")
        existing = await self.session.scalar(select(SkillVersion).where(SkillVersion.skill_id == skill_id, SkillVersion.version == version))
        if existing is not None:
            raise GovernanceValidationError("Published Skill version is immutable")
        record = SkillVersion(tenant_id=subject.tenant_id, skill_id=skill_id, version=version, content_json=draft.content_json, content_digest=draft.content_digest, published_by=subject.user_id)
        self.session.add(record)
        await self.session.flush()
        return record

    async def promote_stable(self, *, subject: SubjectContext, skill_id: str, version: str) -> SkillVersion:
        """Point the stable alias at an existing same-tenant published version.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            skill_id: 用于执行当前操作的 skill id 参数。
            version: 用于执行当前操作的 version 参数。
        """
        _require_role(subject, _PUBLISH_ROLES)
        skill = await self.session.get(SkillDefinition, skill_id)
        record = await self.session.scalar(select(SkillVersion).where(SkillVersion.skill_id == skill_id, SkillVersion.version == version, SkillVersion.tenant_id == subject.tenant_id))
        if skill is None or skill.tenant_id != subject.tenant_id or record is None:
            raise GovernanceValidationError("Published Skill version is unavailable")
        skill.stable_version = version
        await self.session.flush()
        return record

    async def publish_capability(
        self,
        *,
        subject: SubjectContext,
        capability_id: str,
        version: str,
        skill_references: Sequence[str],
        tools: Sequence[str] = (),
        knowledge: Sequence[str] = (),
        model_policy: str = "balanced",
        default_risk: str = "R1",
    ) -> CapabilityVersion:
        """Publish a composition after resolving every Skill reference to a fixed version.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            capability_id: 用于执行当前操作的 capability id 参数。
            version: 用于执行当前操作的 version 参数。
            skill_references: 用于执行当前操作的 skill references 参数。
            tools: 用于执行当前操作的 tools 参数。
            knowledge: 用于执行当前操作的 knowledge 参数。
            model_policy: 用于执行当前操作的 model policy 参数。
            default_risk: 用于执行当前操作的 default risk 参数。
        """
        _require_role(subject, _PUBLISH_ROLES)
        if not _VERSION.fullmatch(version):
            raise GovernanceValidationError("Capability version must be semantic")
        capability = await self.session.get(CapabilityDefinition, capability_id)
        if capability is None or capability.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Capability cannot cross tenants")
        pinned: list[str] = []
        for reference in skill_references:
            key, separator, requested = reference.partition("@")
            if not separator or not requested:
                raise GovernanceValidationError("Skill reference must include a version")
            skill = await self.session.scalar(select(SkillDefinition).where(SkillDefinition.tenant_id == subject.tenant_id, SkillDefinition.key == key))
            fixed = skill.stable_version if skill is not None and requested == "stable" else requested
            published = None if skill is None or fixed is None else await self.session.scalar(select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == fixed))
            if published is None:
                raise GovernanceValidationError("Skill reference is unresolved")
            pinned.append(f"{key}@{fixed}")
        composition = _canonical({"skills": pinned, "tools": sorted(set(tools)), "knowledge": sorted(set(knowledge)), "model_policy": model_policy, "default_risk": default_risk})
        existing = await self.session.scalar(select(CapabilityVersion).where(CapabilityVersion.capability_id == capability_id, CapabilityVersion.version == version))
        if existing is not None:
            raise GovernanceValidationError("Published Capability version is immutable")
        record = CapabilityVersion(tenant_id=subject.tenant_id, capability_id=capability_id, version=version, composition_json=composition, published_by=subject.user_id)
        self.session.add(record)
        await self.session.flush()
        return record

    async def project_published_capabilities(self, *, tenant_id: str, registry: Any) -> tuple[str, ...]:
        """Register declarative published compositions without importing Skill code.

        Args:
            tenant_id: 用于执行当前操作的 tenant id 参数。
            registry: 用于执行当前操作的 registry 参数。
        """
        from agent.capabilities import CapabilityKind, CapabilityMetadata, CapabilityRiskLevel

        rows = tuple(await self.session.scalars(select(CapabilityVersion).where(CapabilityVersion.tenant_id == tenant_id)))
        projected: list[str] = []
        for row in rows:
            definition = await self.session.get(CapabilityDefinition, row.capability_id)
            if definition is None or definition.status != "active":
                continue
            composition = json.loads(row.composition_json)
            capability_id = f"capability.{definition.key}"
            risk = str(composition.get("default_risk", "R1")).replace("R", "L")
            registry.register(
                CapabilityMetadata(
                    id=capability_id,
                    kind=CapabilityKind.SKILL,
                    display_name=definition.display_name,
                    summary=definition.summary or definition.display_name,
                    source=f"governed:{row.version}",
                    enabled=True,
                    risk_level=cast(CapabilityRiskLevel, risk),
                    requires_approval=risk in {"L3", "L4"},
                ),
                loader=lambda value=composition: dict(value),
            )
            projected.append(capability_id)
        return tuple(projected)

    async def validate_distribution(
        self,
        *,
        subject: SubjectContext,
        capability_version_id: str,
        organization_id: str,
    ) -> CapabilityVersion:
        """Authorize distribution of one published version to a same-tenant organization.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            capability_version_id: 用于执行当前操作的 capability version id 参数。
            organization_id: 用于执行当前操作的 organization id 参数。
        """
        _require_role(subject, _DISTRIBUTION_ROLES)
        version = await self.session.get(CapabilityVersion, capability_version_id)
        organization = await self.session.get(Organization, organization_id)
        if version is None or organization is None or {version.tenant_id, organization.tenant_id} != {subject.tenant_id}:
            raise GovernanceValidationError("Capability distribution cannot cross tenants")
        return version


class ConnectorService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """Bind Connector operations to one database transaction.

        Args:
            session: 当前数据库异步会话。
        """
        self.session = session

    async def create_connector(self, *, subject: SubjectContext, key: str, connector_type: str, display_name: str, config: Mapping[str, Any] | None = None) -> Connector:
        """Register a bounded Connector definition for the trusted tenant.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            key: 用于执行当前操作的 key 参数。
            connector_type: 用于执行当前操作的 connector type 参数。
            display_name: 用于执行当前操作的 display name 参数。
            config: 用于执行当前操作的 config 参数。
        """
        _require_role(subject, _CONNECTOR_ROLES)
        if not _KEY.fullmatch(key) or connector_type not in {"MCP", "REST", "FEISHU"}:
            raise GovernanceValidationError("Connector definition is invalid")
        record = Connector(tenant_id=subject.tenant_id, key=key, type=connector_type, display_name=display_name.strip()[:255], config_json=_canonical(dict(config or {})), created_by=subject.user_id)
        self.session.add(record)
        await self.session.flush()
        return record

    async def create_instance(self, *, subject: SubjectContext, connector_id: str, organization_id: str, auth_mode: str, credential_ref: str | None = None) -> ConnectorInstance:
        """Bind a Connector to an organization only within one tenant.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            connector_id: 用于执行当前操作的 connector id 参数。
            organization_id: 用于执行当前操作的 organization id 参数。
            auth_mode: 用于执行当前操作的 auth mode 参数。
            credential_ref: 用于执行当前操作的 credential ref 参数。
        """
        _require_role(subject, _CONNECTOR_ROLES)
        connector = await self.session.get(Connector, connector_id)
        organization = await self.session.get(Organization, organization_id)
        if connector is None or organization is None or {connector.tenant_id, organization.tenant_id} != {subject.tenant_id}:
            raise GovernanceValidationError("Connector instance cannot cross tenants")
        record = ConnectorInstance(tenant_id=subject.tenant_id, connector_id=connector_id, organization_id=organization_id, auth_mode=auth_mode[:32], credential_ref=credential_ref, status="draft")
        self.session.add(record)
        await self.session.flush()
        return record

    async def test_instance(self, *, subject: SubjectContext, instance_id: str, client: ConnectorDiscoveryClient, sensitive_values: Sequence[str] = ()) -> ConnectorInstance:
        """Test an instance and persist only a bounded sanitized failure.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            instance_id: 用于执行当前操作的 instance id 参数。
            client: 用于执行当前操作的 client 参数。
            sensitive_values: 用于执行当前操作的 sensitive values 参数。
        """
        instance = await self._instance(subject, instance_id)
        try:
            await client.test_connection()
        except Exception as exc:
            instance.status = "unavailable"
            instance.last_error = sanitize_text(exc, extra_sensitive_values=sensitive_values)[:1000]
        else:
            instance.status = "available"
            instance.last_error = None
        await self.session.flush()
        return instance

    async def sync_tools(self, *, subject: SubjectContext, instance_id: str, client: ConnectorDiscoveryClient) -> tuple[ConnectorTool, ...]:
        """Synchronize untrusted metadata while preserving reviewed governance fields.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            instance_id: 用于执行当前操作的 instance id 参数。
            client: 用于执行当前操作的 client 参数。
        """
        instance = await self._instance(subject, instance_id)
        try:
            remote = await client.list_tools()
        except Exception as exc:
            instance.status = "unavailable"
            instance.last_error = sanitize_text(exc)[:1000]
            await self.session.flush()
            return ()
        now = datetime.now(UTC)
        existing = {item.external_name: item for item in await self.session.scalars(select(ConnectorTool).where(ConnectorTool.connector_instance_id == instance_id))}
        seen: set[str] = set()
        for item in remote:
            name = str(item.get("name", ""))[:128]
            if not name:
                continue
            seen.add(name)
            record = existing.get(name)
            if record is None:
                record = ConnectorTool(tenant_id=subject.tenant_id, connector_instance_id=instance_id, external_name=name, enabled=False)
                self.session.add(record)
                existing[name] = record
            record.description = str(item.get("description", ""))[:4000]
            raw_schema = item.get("input_schema", {"type": "object"})
            if not isinstance(raw_schema, Mapping) or raw_schema.get("type") != "object":
                raise GovernanceValidationError("Connector Tool schema must describe an object")
            schema = dict(raw_schema)
            schema["additionalProperties"] = False
            record.input_schema_json = _canonical(schema)
            record.provider_version = str(item.get("version", "1"))[:64]
            record.available = True
            record.last_synced_at = now
        for name, record in existing.items():
            if name not in seen:
                record.available = False
        instance.status = "available"
        instance.last_error = None
        await self.session.flush()
        return tuple(existing.values())

    async def review_tool(self, *, subject: SubjectContext, tool_id: str, internal_tool_key: str, risk_level: str, enabled: bool) -> ConnectorTool:
        """Assign administrator-owned stable identity, risk, and enablement.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            tool_id: 用于执行当前操作的 tool id 参数。
            internal_tool_key: 用于执行当前操作的 internal tool key 参数。
            risk_level: 用于执行当前操作的 risk level 参数。
            enabled: 是否启用当前能力。
        """
        _require_role(subject, _CONNECTOR_ROLES)
        tool = await self.session.get(ConnectorTool, tool_id)
        if tool is None or tool.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Connector Tool is unavailable")
        if not _KEY.fullmatch(internal_tool_key) or internal_tool_key.startswith("mcp.") or risk_level not in {"R0", "R1", "R2", "R3", "R4"}:
            raise GovernanceValidationError("Connector Tool review is invalid")
        tool.internal_tool_key = internal_tool_key
        tool.risk_level = risk_level
        tool.enabled = enabled
        await self.session.flush()
        return tool

    async def _instance(self, subject: SubjectContext, instance_id: str) -> ConnectorInstance:
        """Resolve a manageable instance without leaking cross-tenant existence.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            instance_id: 用于执行当前操作的 instance id 参数。
        """
        _require_role(subject, _CONNECTOR_ROLES)
        instance = await self.session.get(ConnectorInstance, instance_id)
        if instance is None or instance.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Connector instance is unavailable")
        return instance


class CredentialService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession, cipher: CredentialCipher) -> None:
        """Bind persistence to an explicit cipher implementation.

        Args:
            session: 当前数据库异步会话。
            cipher: 用于执行当前操作的 cipher 参数。
        """
        self.session = session
        self.cipher = cipher

    async def store(self, *, subject: SubjectContext, scope_type: str, owner_id: str, connector_id: str, plaintext: str) -> Credential:
        """Encrypt and persist a credential after validating tenant and owner scope.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            scope_type: 用于执行当前操作的 scope type 参数。
            owner_id: 用于执行当前操作的 owner id 参数。
            connector_id: 用于执行当前操作的 connector id 参数。
            plaintext: 用于执行当前操作的 plaintext 参数。
        """
        _require_role(subject, _CONNECTOR_ROLES)
        connector = await self.session.get(Connector, connector_id)
        if connector is None or connector.tenant_id != subject.tenant_id or scope_type not in {"USER", "ORGANIZATION", "SYSTEM"} or not owner_id:
            raise GovernanceValidationError("Credential scope is invalid")
        if scope_type == "USER" and owner_id != subject.user_id and GovernedRole.ENTERPRISE_ADMIN not in subject.roles:
            raise GovernanceValidationError("Credential owner is not authorized")
        if scope_type == "ORGANIZATION":
            organization = await self.session.get(Organization, owner_id)
            if organization is None or organization.tenant_id != subject.tenant_id:
                raise GovernanceValidationError("Credential organization cannot cross tenants")
        record = Credential(tenant_id=subject.tenant_id, scope_type=scope_type, owner_id=owner_id, connector_id=connector_id, encrypted_payload=self.cipher.encrypt(plaintext), status="active")
        self.session.add(record)
        await self.session.flush()
        return record

    async def resolve_for_execution(self, *, subject: SubjectContext, credential_id: str, connector_id: str) -> str:
        """Return plaintext ephemerally after exact tenant, owner, and status checks.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            credential_id: 用于执行当前操作的 credential id 参数。
            connector_id: 用于执行当前操作的 connector id 参数。
        """
        record = await self.session.get(Credential, credential_id)
        if record is None or record.tenant_id != subject.tenant_id or record.connector_id != connector_id or record.status != "active":
            raise GovernanceValidationError("Credential is unavailable")
        if record.scope_type == "USER" and record.owner_id != subject.user_id:
            raise GovernanceValidationError("Credential is unavailable")
        if record.scope_type == "ORGANIZATION" and record.owner_id not in subject.organization_ids:
            raise GovernanceValidationError("Credential is unavailable")
        try:
            return self.cipher.decrypt(record.encrypted_payload)
        except Exception as exc:
            raise GovernanceValidationError("Credential cannot be resolved") from exc
