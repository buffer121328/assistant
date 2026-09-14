from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
import json
from typing import Mapping, Protocol


LOCAL_TENANT_ID = "local"
LOCAL_ORGANIZATION_ID = "local-root"
PROFILE_SCHEMA_VERSION = "governed-agent-profile.v1"
MAX_PROFILE_SNAPSHOT_BYTES = 32_000


class GovernedRole(StrEnum):
    """定义当前组件的职责和边界。"""

    MEMBER = "member"
    MANAGER = "manager"
    DEPARTMENT_ADMIN = "department_admin"
    ENTERPRISE_ADMIN = "enterprise_admin"
    CAPABILITY_PUBLISHER = "capability_publisher"
    CONNECTOR_ADMIN = "connector_admin"
    AUDITOR = "auditor"


class ResourceVisibility(StrEnum):
    """定义当前组件的职责和边界。"""

    PRIVATE = "private"
    DEPARTMENT = "department"
    ORGANIZATION = "organization"


class PolicyEffect(StrEnum):
    """定义当前组件的职责和边界。"""

    ALLOW = "ALLOW"
    DENY = "DENY"
    CONFIRM = "CONFIRM"
    APPROVAL = "APPROVAL"


class ToolRisk(StrEnum):
    """定义当前组件的职责和边界。"""

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class GovernanceValidationError(ValueError):
    """定义当前组件可安全处理的错误类型。"""


@dataclass(frozen=True)
class SubjectContext:
    """定义当前组件的职责和边界。"""

    tenant_id: str  # tenant_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    organization_ids: tuple[str, ...] = ()  # organization_ids 对应的数据字段。
    roles: tuple[GovernedRole, ...] = (GovernedRole.MEMBER,)  # roles 对应的数据字段。
    device_id: str | None = None  # device_id 对应的数据字段。
    session_id: str | None = None  # session_id 对应的数据字段。
    authority_revision: int = 0  # authority_revision 对应的数据字段。

    def __post_init__(self) -> None:
        """Validate and canonicalize trusted identity fields."""
        if not self.tenant_id.strip() or not self.user_id.strip():
            raise GovernanceValidationError("Subject tenant and user are required")
        object.__setattr__(
            self, "organization_ids", tuple(dict.fromkeys(self.organization_ids))
        )
        object.__setattr__(self, "roles", tuple(dict.fromkeys(self.roles)))
        if self.authority_revision < 0:
            raise GovernanceValidationError("Authority revision must not be negative")


@dataclass(frozen=True)
class ResourceScope:
    """定义当前组件的职责和边界。"""

    tenant_id: str  # tenant_id 对应的数据字段。
    organization_id: str | None  # organization_id 对应的数据字段。
    owner_type: str  # owner_type 对应的数据字段。
    owner_id: str  # owner_id 对应的数据字段。
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE  # visibility 对应的数据字段。

    def __post_init__(self) -> None:
        """Reject incomplete or unsupported scope data before authorization."""
        if not self.tenant_id.strip() or not self.owner_type.strip() or not self.owner_id:
            raise GovernanceValidationError("Resource tenant and owner are required")
        if self.visibility is ResourceVisibility.DEPARTMENT and not self.organization_id:
            raise GovernanceValidationError(
                "Department visibility requires an organization"
            )


@dataclass(frozen=True)
class ResourceContext:
    """定义当前组件的职责和边界。"""

    resource_type: str  # resource_type 对应的数据字段。
    resource_id: str  # resource_id 对应的数据字段。
    scope: ResourceScope  # scope 对应的数据字段。
    attributes: Mapping[str, object] | None = None  # attributes 对应的数据字段。

    def __post_init__(self) -> None:
        """Require a stable resource type and identifier."""
        if not self.resource_type.strip() or not self.resource_id.strip():
            raise GovernanceValidationError("Resource type and id are required")


@dataclass(frozen=True)
class PolicyStatement:
    """定义当前组件的职责和边界。"""

    effect: PolicyEffect  # effect 对应的数据字段。
    action: str  # action 对应的数据字段。
    scope_type: str = "enterprise"  # scope_type 对应的数据字段。
    scope_id: str | None = None  # scope_id 对应的数据字段。
    resource_type: str = "*"  # resource_type 对应的数据字段。
    constraints: Mapping[str, Mapping[str, object]] | None = None  # constraints 对应的数据字段。
    priority: int = 0  # priority 对应的数据字段。


@dataclass(frozen=True)
class PolicyDecision:
    """定义当前组件的职责和边界。"""

    effect: PolicyEffect  # effect 对应的数据字段。
    reason_code: str  # reason_code 对应的数据字段。


@dataclass(frozen=True)
class CapabilityGrantView:
    """定义当前组件的职责和边界。"""

    tenant_id: str  # tenant_id 对应的数据字段。
    source_organization_id: str  # source_organization_id 对应的数据字段。
    target_organization_id: str  # target_organization_id 对应的数据字段。
    capability_key: str  # capability_key 对应的数据字段。
    version: str  # version 对应的数据字段。
    can_delegate: bool = False  # can_delegate 对应的数据字段。
    max_delegate_depth: int = 0  # max_delegate_depth 对应的数据字段。
    constraints: Mapping[str, object] | None = None  # constraints 对应的数据字段。
    expires_at: datetime | None = None  # expires_at 对应的数据字段。
    status: str = "active"  # status 对应的数据字段。

    def is_active(self, *, now: datetime | None = None) -> bool:
        """Return whether status and expiry still contribute authority.

        Args:
            now: 用于执行当前操作的 now 参数。
        """
        current = now or datetime.now(UTC)
        expiry = self.expires_at
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return self.status == "active" and (expiry is None or expiry > current)


@dataclass(frozen=True)
class GovernedAgentProfile:
    """定义当前组件的职责和边界。"""

    subject: SubjectContext  # subject 对应的数据字段。
    capabilities: tuple[str, ...] = ()  # capabilities 对应的数据字段。
    skills: tuple[str, ...] = ()  # skills 对应的数据字段。
    tools: tuple[str, ...] = ()  # tools 对应的数据字段。
    knowledge_scopes: tuple[str, ...] = ()  # knowledge_scopes 对应的数据字段。
    memory_access: tuple[tuple[str, str], ...] = (("personal", "read_write"),)  # memory_access 对应的数据字段。
    max_runtime_seconds: int = 1800  # max_runtime_seconds 对应的数据字段。
    max_cost_usd: float = 5.0  # max_cost_usd 对应的数据字段。
    model_policy: tuple[tuple[str, str], ...] = (
        ("interactive", "fast"),
        ("durable", "strong"),
    )  # model_policy 对应的数据字段。
    schema_version: str = PROFILE_SCHEMA_VERSION  # schema_version 对应的数据字段。

    def to_snapshot(self) -> str:
        """执行当前组件定义的业务处理逻辑。"""
        payload = asdict(self)
        payload["subject"]["roles"] = [role.value for role in self.subject.roles]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > MAX_PROFILE_SNAPSHOT_BYTES:
            raise GovernanceValidationError("Governed Agent Profile snapshot is too large")
        return encoded

    @classmethod
    def from_snapshot(cls, snapshot: str) -> GovernedAgentProfile:
        """Parse and validate a stored governed profile snapshot.

        Args:
            snapshot: 用于执行当前操作的 snapshot 参数。
        """
        if len(snapshot.encode("utf-8")) > MAX_PROFILE_SNAPSHOT_BYTES:
            raise GovernanceValidationError("Governed Agent Profile snapshot is too large")
        try:
            payload = json.loads(snapshot)
            subject_payload = payload["subject"]
            subject = SubjectContext(
                tenant_id=str(subject_payload["tenant_id"]),
                user_id=str(subject_payload["user_id"]),
                organization_ids=tuple(subject_payload.get("organization_ids", ())),
                roles=tuple(GovernedRole(item) for item in subject_payload.get("roles", ())),
                device_id=subject_payload.get("device_id"),
                session_id=subject_payload.get("session_id"),
                authority_revision=int(subject_payload.get("authority_revision", 0)),
            )
            return cls(
                subject=subject,
                capabilities=tuple(payload.get("capabilities", ())),
                skills=tuple(payload.get("skills", ())),
                tools=tuple(payload.get("tools", ())),
                knowledge_scopes=tuple(payload.get("knowledge_scopes", ())),
                memory_access=tuple(tuple(item) for item in payload.get("memory_access", ())),
                max_runtime_seconds=int(payload.get("max_runtime_seconds", 1800)),
                max_cost_usd=float(payload.get("max_cost_usd", 5.0)),
                model_policy=tuple(tuple(item) for item in payload.get("model_policy", ())),
                schema_version=str(payload["schema_version"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GovernanceValidationError("Invalid governed profile snapshot") from exc


@dataclass(frozen=True)
class AgentDefinition:
    """定义当前组件的职责和边界。"""

    id: str  # id 对应的数据字段。
    required_capabilities: tuple[str, ...] = ()  # required_capabilities 对应的数据字段。
    required_tools: tuple[str, ...] = ()  # required_tools 对应的数据字段。
    required_knowledge_scopes: tuple[str, ...] = ()  # required_knowledge_scopes 对应的数据字段。
    required_memory_access: tuple[tuple[str, str], ...] = ()  # required_memory_access 对应的数据字段。
    max_runtime_seconds: int = 300  # max_runtime_seconds 对应的数据字段。
    max_cost_usd: float = 1.0  # max_cost_usd 对应的数据字段。

    def __post_init__(self) -> None:
        """Reject invalid definitions before they participate in delegation."""
        if not self.id.strip():
            raise GovernanceValidationError("Agent definition id is required")
        if self.max_runtime_seconds <= 0 or self.max_cost_usd < 0:
            raise GovernanceValidationError("Agent definition budget is invalid")


def derive_child_agent_profile(
    parent: GovernedAgentProfile,
    definition: AgentDefinition,
) -> GovernedAgentProfile:
    """Intersect delegated requirements with the parent's current authority.

    Args:
        parent: 用于执行当前操作的 parent 参数。
        definition: 用于执行当前操作的 definition 参数。
    """
    capabilities = _ordered_intersection(parent.capabilities, definition.required_capabilities)
    tools = _ordered_intersection(parent.tools, definition.required_tools)
    knowledge = _ordered_intersection(
        parent.knowledge_scopes, definition.required_knowledge_scopes
    )
    memory = tuple(
        item for item in definition.required_memory_access if item in parent.memory_access
    )
    if definition.required_capabilities and not capabilities:
        raise GovernanceValidationError("Parent lacks delegated capability authority")
    if definition.required_tools and not tools:
        raise GovernanceValidationError("Parent lacks delegated tool authority")
    return GovernedAgentProfile(
        subject=parent.subject,
        capabilities=capabilities,
        skills=parent.skills,
        tools=tools,
        knowledge_scopes=knowledge,
        memory_access=memory,
        max_runtime_seconds=min(parent.max_runtime_seconds, definition.max_runtime_seconds),
        max_cost_usd=min(parent.max_cost_usd, definition.max_cost_usd),
        model_policy=parent.model_policy,
    )


def _ordered_intersection(parent: tuple[str, ...], required: tuple[str, ...]) -> tuple[str, ...]:
    """Return required values that are already present in parent authority.

    Args:
        parent: 用于执行当前操作的 parent 参数。
        required: 用于执行当前操作的 required 参数。
    """
    allowed = set(parent)
    return tuple(dict.fromkeys(item for item in required if item in allowed))


class IdentityVerifier(Protocol):
    """定义当前组件的接口契约。"""

    async def verify(self, token: str) -> SubjectContext:
        """Return verified claims or raise without falling back to local identity.

        Args:
            token: 用于执行当前操作的 token 参数。
        """
        ...


async def verified_subject(
    token: str, *, verifier: IdentityVerifier | None
) -> SubjectContext:
    """Resolve production identity strictly through a configured verifier.

    Args:
        token: 用于执行当前操作的 token 参数。
        verifier: 用于执行当前操作的 verifier 参数。
    """
    if verifier is None or not token.strip():
        raise GovernanceValidationError("Production identity verifier is unavailable")
    subject = await verifier.verify(token)
    if not isinstance(subject, SubjectContext):
        raise GovernanceValidationError("Identity verifier returned invalid claims")
    return subject


def local_subject(
    user_id: str,
    *,
    organization_ids: tuple[str, ...] = (LOCAL_ORGANIZATION_ID,),
    roles: tuple[GovernedRole, ...] = (GovernedRole.MEMBER,),
    authority_revision: int = 0,
) -> SubjectContext:
    """Build the deterministic server-side identity used only by local mode.

    Args:
        user_id: 目标用户 ID。
        organization_ids: 用于执行当前操作的 organization ids 参数。
        roles: 用于执行当前操作的 roles 参数。
        authority_revision: 用于执行当前操作的 authority revision 参数。
    """
    return SubjectContext(
        tenant_id=LOCAL_TENANT_ID,
        user_id=user_id,
        organization_ids=organization_ids,
        roles=roles,
        authority_revision=authority_revision,
    )


def normalize_tool_risk(value: str | ToolRisk) -> ToolRisk:
    """Map legacy L0-L4 or normalized R0-R4 metadata to the governed enum.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    raw = str(value.value if isinstance(value, ToolRisk) else value).upper()
    if raw.startswith("L") and raw[1:] in {"0", "1", "2", "3", "4"}:
        raw = f"R{raw[1:]}"
    try:
        return ToolRisk(raw)
    except ValueError as exc:
        raise GovernanceValidationError("Unsupported tool risk") from exc


def default_risk_effect(risk: str | ToolRisk) -> PolicyEffect:
    """Return the VNext default outcome for a normalized risk level.

    Args:
        risk: 用于执行当前操作的 risk 参数。
    """
    return {
        ToolRisk.R0: PolicyEffect.ALLOW,
        ToolRisk.R1: PolicyEffect.ALLOW,
        ToolRisk.R2: PolicyEffect.DENY,
        ToolRisk.R3: PolicyEffect.CONFIRM,
        ToolRisk.R4: PolicyEffect.APPROVAL,
    }[normalize_tool_risk(risk)]


def resource_scope_allows(subject: SubjectContext, scope: ResourceScope) -> bool:
    """Enforce tenant and visibility boundaries before policy evaluation.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        scope: 用于执行当前操作的 scope 参数。
    """
    if subject.tenant_id != scope.tenant_id:
        return False
    if scope.visibility is ResourceVisibility.PRIVATE:
        return scope.owner_type == "user" and scope.owner_id == subject.user_id
    if scope.visibility is ResourceVisibility.DEPARTMENT:
        return bool(scope.organization_id in subject.organization_ids)
    return True


def authorize(
    *,
    subject: SubjectContext,
    action: str,
    resource: ResourceContext,
    context: Mapping[str, object] | None = None,
    statements: tuple[PolicyStatement, ...] = (),
    risk: str | ToolRisk | None = None,
) -> PolicyDecision:
    """Evaluate scope, safe constraints, deny precedence, and risk defaults.

    Args:
        subject: 用于执行当前操作的 subject 参数。
        action: 用于执行当前操作的 action 参数。
        resource: 用于执行当前操作的 resource 参数。
        context: 用于执行当前操作的 context 参数。
        statements: 用于执行当前操作的 statements 参数。
        risk: 用于执行当前操作的 risk 参数。
    """
    if not action.strip() or not resource_scope_allows(subject, resource.scope):
        return PolicyDecision(PolicyEffect.DENY, "resource_scope_denied")
    runtime = dict(context or {})
    applicable = tuple(
        statement
        for statement in statements
        if _statement_applies(statement, subject, action, resource, runtime)
    )
    if any(statement.effect is PolicyEffect.DENY for statement in applicable):
        return PolicyDecision(PolicyEffect.DENY, "explicit_deny")
    if applicable:
        selected = max(applicable, key=_statement_rank)
        return PolicyDecision(selected.effect, "policy_match")
    if risk is not None:
        return PolicyDecision(default_risk_effect(risk), "risk_default")
    return PolicyDecision(PolicyEffect.DENY, "no_applicable_policy")


def validate_delegation(
    parent: CapabilityGrantView,
    child: CapabilityGrantView,
    *,
    now: datetime | None = None,
) -> None:
    """Reject cross-tenant, capability, depth, expiry, or constraint amplification.

    Args:
        parent: 用于执行当前操作的 parent 参数。
        child: 用于执行当前操作的 child 参数。
        now: 用于执行当前操作的 now 参数。
    """
    if not parent.is_active(now=now):
        raise GovernanceValidationError("Source capability grant is inactive")
    if parent.tenant_id != child.tenant_id:
        raise GovernanceValidationError("Capability grant cannot cross tenants")
    if parent.target_organization_id != child.source_organization_id:
        raise GovernanceValidationError("Delegation source lacks the capability")
    if (parent.capability_key, parent.version) != (
        child.capability_key,
        child.version,
    ):
        raise GovernanceValidationError("Delegation capability or version differs")
    if not parent.can_delegate or parent.max_delegate_depth <= 0:
        raise GovernanceValidationError("Source capability grant cannot delegate")
    if child.max_delegate_depth >= parent.max_delegate_depth:
        raise GovernanceValidationError("Delegation depth cannot be amplified")
    if not _constraints_narrower(parent.constraints or {}, child.constraints or {}):
        raise GovernanceValidationError("Delegation constraints cannot be broadened")


def _statement_applies(
    statement: PolicyStatement,
    subject: SubjectContext,
    action: str,
    resource: ResourceContext,
    context: Mapping[str, object],
) -> bool:
    """Match a statement using allowlisted fields and operators only.

    Args:
        statement: 用于执行当前操作的 statement 参数。
        subject: 用于执行当前操作的 subject 参数。
        action: 用于执行当前操作的 action 参数。
        resource: 用于执行当前操作的 resource 参数。
        context: 用于执行当前操作的 context 参数。
    """
    if statement.action not in {"*", action}:
        return False
    if statement.resource_type not in {"*", resource.resource_type}:
        return False
    if statement.scope_type == "organization" and statement.scope_id not in subject.organization_ids:
        return False
    if statement.scope_type == "role" and statement.scope_id not in {
        role.value for role in subject.roles
    }:
        return False
    if statement.scope_type == "user" and statement.scope_id != subject.user_id:
        return False
    if statement.scope_type not in {"enterprise", "organization", "role", "user"}:
        return False
    return _constraints_match(statement.constraints or {}, context)


def _constraints_match(
    constraints: Mapping[str, Mapping[str, object]],
    context: Mapping[str, object],
) -> bool:
    """Evaluate a deliberately small non-executable constraint vocabulary.

    Args:
        constraints: 用于执行当前操作的 constraints 参数。
        context: 用于执行当前操作的 context 参数。
    """
    for attribute, condition in constraints.items():
        if attribute not in context or not isinstance(condition, Mapping):
            return False
        operator = condition.get("op")
        expected = condition.get("value")
        actual = context[attribute]
        try:
            if operator == "eq" and actual != expected:
                return False
            if operator == "in" and actual not in expected:  # type: ignore[operator]
                return False
            if operator == "lte" and not _numeric(actual) <= _numeric(expected):
                return False
            if operator == "gte" and not _numeric(actual) >= _numeric(expected):
                return False
            if operator not in {"eq", "in", "lte", "gte"}:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _statement_rank(statement: PolicyStatement) -> tuple[int, int]:
    """Rank matching non-deny statements by specificity and explicit priority.

    Args:
        statement: 用于执行当前操作的 statement 参数。
    """
    specificity = {"enterprise": 1, "organization": 3, "role": 4, "user": 5}
    return specificity.get(statement.scope_type, 0), statement.priority


def _numeric(value: object) -> float:
    """Convert only explicit JSON-compatible numeric values for comparisons.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError("Policy comparison value is not numeric")
    return float(value)


def _constraints_narrower(
    parent: Mapping[str, object], child: Mapping[str, object]
) -> bool:
    """Accept identical values or lower numeric ceilings without dropping constraints.

    Args:
        parent: 用于执行当前操作的 parent 参数。
        child: 用于执行当前操作的 child 参数。
    """
    for key, parent_value in parent.items():
        if key not in child:
            return False
        child_value = child[key]
        if isinstance(parent_value, (int, float)) and isinstance(
            child_value, (int, float)
        ):
            if child_value > parent_value:
                return False
        elif child_value != parent_value:
            return False
    return True


__all__ = [
    "AgentDefinition",
    "CapabilityGrantView",
    "GovernanceValidationError",
    "GovernedAgentProfile",
    "GovernedRole",
    "IdentityVerifier",
    "LOCAL_ORGANIZATION_ID",
    "LOCAL_TENANT_ID",
    "MAX_PROFILE_SNAPSHOT_BYTES",
    "PROFILE_SCHEMA_VERSION",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyStatement",
    "ResourceContext",
    "ResourceScope",
    "ResourceVisibility",
    "SubjectContext",
    "ToolRisk",
    "authorize",
    "default_risk_effect",
    "derive_child_agent_profile",
    "local_subject",
    "normalize_tool_risk",
    "resource_scope_allows",
    "validate_delegation",
    "verified_subject",
]
