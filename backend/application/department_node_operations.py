"""Governed department-node identity, diagnostics, configuration, and operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import re
import secrets
from typing import Callable

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.enterprise_runtime import GovernanceAuditInput, GovernanceAuditService
from application.task_execution.events import TaskEventRepository
from application.task_execution.lifecycle import TASK_EVENT_STATUS
from domain.models import (
    CapabilityDefinition,
    CapabilityGrant,
    CapabilityVersion,
    DepartmentNode,
    DiagnosticPackage,
    NodeConfigApplication,
    NodeConfigSnapshot,
    NodeEnrollment,
    NodeRemoteOperation,
    Organization,
    PolicyRule,
    Task,
    TaskContextSnapshot,
    Tenant,
)
from domain.policies.enterprise import GovernedRole, GovernanceValidationError, SubjectContext

MAX_DIAGNOSTIC_DAYS = 7
MAX_DIAGNOSTIC_RECORDS = 200
MAX_DIAGNOSTIC_BYTES = 256_000
ONLINE_SECONDS = 90
STALE_SECONDS = 300
_NODE_OPERATIONS = {
    "refresh_config",
    "resync_config",
    "pause_new_work",
    "resume_new_work",
    "stop_task",
    "restart_agent",
}
_TERMINAL_TASK_STATUSES = {"success", "failed", "cancelled"}
_TERMINAL_OPERATION_STATUSES = {"succeeded", "failed", "expired"}
_SENSITIVE_MARKERS = ("authorization:", "bearer ", "password=", "token=", "api_key", "cookie:")
_ABSOLUTE_PATH = re.compile(r"(?:(?:[A-Za-z]:\\)|/)(?:[^\s/\\]+[/\\]){1,}[^\s]*")


def _utc(value: datetime) -> datetime:
    """Normalize database and request timestamps for stable comparisons."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _canonical(value: object) -> str:
    """Serialize governed payloads deterministically for revisions and checksums."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    """Hash an opaque secret or immutable manifest without persisting plaintext."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_summary(value: str | None, *, limit: int = 500) -> str:
    """Bound and redact common reusable-secret markers and absolute paths."""
    text = (value or "").strip()[:limit]
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return "[REDACTED]"
    return _ABSOLUTE_PATH.sub("[PATH]", text)


class DepartmentNodeOperationsService:
    """Orchestrate node and IT operations inside one trusted database transaction."""

    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] | None = None) -> None:
        """Bind persistence and an injectable UTC clock for expiry and heartbeat rules."""
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        """Return a timezone-aware current timestamp."""
        return _utc(self._clock())

    @staticmethod
    def _require_role(subject: SubjectContext, *roles: GovernedRole) -> None:
        """Require at least one explicit management role from server-resolved identity."""
        if not set(subject.roles).intersection(roles):
            raise GovernanceValidationError("Administrative role is required")

    @staticmethod
    def _authorize_organization(subject: SubjectContext, organization_id: str) -> None:
        """Prevent non-enterprise administrators from escaping their organization scope."""
        if (
            GovernedRole.ENTERPRISE_ADMIN not in subject.roles
            and GovernedRole.AUDITOR not in subject.roles
            and organization_id not in subject.organization_ids
        ):
            raise GovernanceValidationError("Organization is unavailable")

    async def _organization(self, tenant_id: str, organization_id: str) -> Organization:
        """Resolve one active same-tenant organization without leaking foreign metadata."""
        item = await self._session.get(Organization, organization_id)
        if item is None or item.tenant_id != tenant_id or item.status != "active":
            raise GovernanceValidationError("Organization is unavailable")
        return item

    async def _audit(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        result_status: str,
        organization_id: str | None,
        resource_type: str,
        resource_id: str,
        summary: str,
        task_id: str | None = None,
    ) -> None:
        """Append one bounded governance fact for node or diagnostic activity."""
        await GovernanceAuditService(self._session).record(
            GovernanceAuditInput(
                tenant_id=tenant_id,
                subject_id=subject_id,
                organization_id=organization_id,
                task_id=task_id,
                resource_type=resource_type,
                resource_id=resource_id,
                policy_decision="ALLOW",
                result_status=result_status,
                summary=_safe_summary(summary),
            )
        )

    async def create_enrollment(
        self, subject: SubjectContext, *, organization_id: str, expires_in_minutes: int
    ) -> dict[str, object]:
        """Create a single-use enrollment and return its plaintext secret exactly once."""
        self._require_role(subject, GovernedRole.ENTERPRISE_ADMIN)
        await self._organization(subject.tenant_id, organization_id)
        if not 5 <= expires_in_minutes <= 1_440:
            raise GovernanceValidationError("Enrollment expiry is invalid")
        secret = secrets.token_urlsafe(32)
        item = NodeEnrollment(
            tenant_id=subject.tenant_id,
            organization_id=organization_id,
            secret_digest=_digest(secret),
            expires_at=self._now() + timedelta(minutes=expires_in_minutes),
            created_by=subject.user_id,
        )
        self._session.add(item)
        await self._session.flush()
        await self._audit(
            tenant_id=subject.tenant_id,
            subject_id=subject.user_id,
            result_status="node_enrollment_created",
            organization_id=organization_id,
            resource_type="node_enrollment",
            resource_id=item.id,
            summary="Department node enrollment created.",
        )
        return {
            "id": item.id,
            "organization_id": item.organization_id,
            "status": item.status,
            "expires_at": item.expires_at,
            "enrollment_token": f"{item.id}.{secret}",
        }

    async def register_node(
        self, *, enrollment_token: str, name: str, agent_version: str
    ) -> dict[str, object]:
        """Consume one enrollment and return a revocable node credential once."""
        enrollment_id, secret = self._parse_token(enrollment_token)
        enrollment = await self._session.get(NodeEnrollment, enrollment_id)
        now = self._now()
        if (
            enrollment is None
            or enrollment.status != "active"
            or _utc(enrollment.expires_at) <= now
            or not hmac.compare_digest(enrollment.secret_digest, _digest(secret))
        ):
            raise GovernanceValidationError("Node enrollment is invalid")
        bounded_name = name.strip()
        bounded_version = agent_version.strip()
        if not bounded_name or len(bounded_name) > 120 or not bounded_version or len(bounded_version) > 64:
            raise GovernanceValidationError("Node registration fields are invalid")
        duplicate = await self._session.scalar(
            select(DepartmentNode.id).where(
                DepartmentNode.tenant_id == enrollment.tenant_id,
                DepartmentNode.organization_id == enrollment.organization_id,
                DepartmentNode.name == bounded_name,
            )
        )
        if duplicate is not None:
            raise GovernanceValidationError("Node registration fields are invalid")
        credential = secrets.token_urlsafe(32)
        node = DepartmentNode(
            tenant_id=enrollment.tenant_id,
            organization_id=enrollment.organization_id,
            name=bounded_name,
            credential_digest=_digest(credential),
            agent_version=bounded_version,
        )
        enrollment.status = "used"
        enrollment.used_at = now
        self._session.add(node)
        await self._session.flush()
        await self._audit(
            tenant_id=node.tenant_id,
            subject_id=node.id,
            result_status="node_registered",
            organization_id=node.organization_id,
            resource_type="department_node",
            resource_id=node.id,
            summary="Department node registered.",
        )
        return {
            "node": self._node_payload(node, now=now),
            "node_token": f"{node.id}.{credential}",
        }

    async def revoke_enrollment(
        self, subject: SubjectContext, *, enrollment_id: str
    ) -> dict[str, object]:
        """Revoke one unused same-tenant enrollment without returning its secret digest."""
        self._require_role(subject, GovernedRole.ENTERPRISE_ADMIN)
        item = await self._session.get(NodeEnrollment, enrollment_id)
        if item is None or item.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Node enrollment is unavailable")
        if item.status == "active":
            item.status = "revoked"
            await self._audit(
                tenant_id=subject.tenant_id,
                subject_id=subject.user_id,
                result_status="node_enrollment_revoked",
                organization_id=item.organization_id,
                resource_type="node_enrollment",
                resource_id=item.id,
                summary="Department node enrollment revoked.",
            )
        return {
            "id": item.id,
            "organization_id": item.organization_id,
            "status": item.status,
            "expires_at": _utc(item.expires_at),
        }

    @staticmethod
    def _parse_token(token: str) -> tuple[str, str]:
        """Split a bounded opaque token into public lookup identity and secret."""
        if not token or len(token) > 256 or "." not in token:
            raise GovernanceValidationError("Node credential is invalid")
        identity, secret = token.split(".", maxsplit=1)
        if not identity or not secret:
            raise GovernanceValidationError("Node credential is invalid")
        return identity, secret

    async def authenticate_node(self, token: str) -> DepartmentNode:
        """Resolve one active node using constant-time digest comparison."""
        node_id, secret = self._parse_token(token)
        node = await self._session.get(DepartmentNode, node_id)
        if (
            node is None
            or node.status != "active"
            or not hmac.compare_digest(node.credential_digest, _digest(secret))
        ):
            raise GovernanceValidationError("Node credential is invalid")
        return node

    async def revoke_node(self, subject: SubjectContext, *, node_id: str) -> dict[str, object]:
        """Revoke one visible node so its credential immediately stops working."""
        self._require_role(subject, GovernedRole.ENTERPRISE_ADMIN)
        node = await self._admin_node(subject, node_id)
        node.status = "revoked"
        await self._audit(
            tenant_id=subject.tenant_id,
            subject_id=subject.user_id,
            result_status="node_revoked",
            organization_id=node.organization_id,
            resource_type="department_node",
            resource_id=node.id,
            summary="Department node revoked.",
        )
        return self._node_payload(node, now=self._now())

    async def heartbeat(
        self,
        node: DepartmentNode,
        *,
        agent_version: str,
        applied_config_revision: int,
        accepts_new_work: bool,
        health_status: str,
        health_summary: str,
    ) -> dict[str, object]:
        """Persist bounded runtime health facts from an authenticated active node."""
        if (
            not agent_version.strip()
            or len(agent_version) > 64
            or applied_config_revision < 0
            or applied_config_revision > node.desired_config_revision
        ):
            raise GovernanceValidationError("Heartbeat is invalid")
        if health_status not in {"healthy", "degraded", "error", "unknown"}:
            raise GovernanceValidationError("Heartbeat health status is invalid")
        node.agent_version = agent_version.strip()
        node.applied_config_revision = applied_config_revision
        node.accepts_new_work = accepts_new_work
        node.health_status = health_status
        node.health_summary = _safe_summary(health_summary)
        node.last_seen_at = self._now()
        await self._session.flush()
        return self._node_payload(node, now=node.last_seen_at)

    async def list_nodes(self, subject: SubjectContext) -> tuple[dict[str, object], ...]:
        """List node health and drift within the subject's tenant and role scope."""
        self._require_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.DEPARTMENT_ADMIN)
        statement = select(DepartmentNode).where(DepartmentNode.tenant_id == subject.tenant_id)
        if GovernedRole.ENTERPRISE_ADMIN not in subject.roles:
            statement = statement.where(DepartmentNode.organization_id.in_(subject.organization_ids))
        rows = tuple(await self._session.scalars(statement.order_by(DepartmentNode.name)))
        now = self._now()
        names = await self._organization_names(subject.tenant_id, {row.organization_id for row in rows})
        return tuple({**self._node_payload(row, now=now), "organization_name": names.get(row.organization_id, "")} for row in rows)

    async def _organization_names(self, tenant_id: str, ids: set[str]) -> dict[str, str]:
        """Resolve safe organization labels only inside the trusted tenant."""
        if not ids:
            return {}
        rows = tuple(await self._session.scalars(select(Organization).where(Organization.tenant_id == tenant_id, Organization.id.in_(ids))))
        return {row.id: row.name for row in rows}

    def _node_payload(self, node: DepartmentNode, *, now: datetime) -> dict[str, object]:
        """Serialize safe node facts without credential material."""
        state = "offline"
        if node.status == "revoked":
            state = "revoked"
        elif node.last_seen_at is not None:
            age = (now - _utc(node.last_seen_at)).total_seconds()
            state = "online" if age <= ONLINE_SECONDS else "stale" if age <= STALE_SECONDS else "offline"
        return {
            "id": node.id,
            "organization_id": node.organization_id,
            "name": node.name,
            "status": node.status,
            "connection_state": state,
            "agent_version": node.agent_version,
            "desired_config_revision": node.desired_config_revision,
            "applied_config_revision": node.applied_config_revision,
            "configuration_drift": node.desired_config_revision != node.applied_config_revision,
            "accepts_new_work": node.accepts_new_work,
            "health_status": node.health_status,
            "health_summary": node.health_summary,
            "last_seen_at": _utc(node.last_seen_at) if node.last_seen_at else None,
        }

    async def _admin_node(self, subject: SubjectContext, node_id: str) -> DepartmentNode:
        """Resolve one node only after tenant and organization-scope checks."""
        node = await self._session.get(DepartmentNode, node_id)
        if node is None or node.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Node is unavailable")
        self._authorize_organization(subject, node.organization_id)
        return node

    async def desired_configuration(self, node: DepartmentNode) -> dict[str, object]:
        """Build or reuse the canonical active-capability snapshot for a node's department."""
        rows = tuple(
            (await self._session.execute(
                select(CapabilityGrant, CapabilityDefinition, CapabilityVersion)
                .join(CapabilityDefinition, CapabilityDefinition.id == CapabilityGrant.capability_id)
                .join(
                    CapabilityVersion,
                    (CapabilityVersion.capability_id == CapabilityGrant.capability_id)
                    & (CapabilityVersion.version == CapabilityGrant.version_constraint),
                )
                .where(
                    CapabilityGrant.tenant_id == node.tenant_id,
                    CapabilityGrant.target_organization_id == node.organization_id,
                    CapabilityGrant.status == "active",
                    CapabilityDefinition.status == "active",
                )
                .order_by(CapabilityDefinition.key, CapabilityVersion.version)
            )).all()
        )
        policy_rows = tuple(
            await self._session.scalars(
                select(PolicyRule).where(
                    PolicyRule.tenant_id == node.tenant_id,
                    PolicyRule.status == "active",
                    or_(PolicyRule.scope_id.is_(None), PolicyRule.scope_id == node.organization_id),
                ).order_by(PolicyRule.priority.desc(), PolicyRule.action, PolicyRule.id)
            )
        )
        tenant = await self._session.get(Tenant, node.tenant_id)
        payload = {
            "organization_id": node.organization_id,
            "authority_revision": tenant.authority_revision if tenant else 0,
            "capabilities": [
                {
                    "capability_id": definition.id,
                    "capability_version_id": version.id,
                    "key": definition.key,
                    "version": version.version,
                    "display_name": definition.display_name,
                    "composition": self._safe_composition(version.composition_json),
                }
                for _grant, definition, version in rows
            ],
            "policies": [
                {
                    "id": policy.id,
                    "scope_type": policy.scope_type,
                    "scope_id": policy.scope_id,
                    "action": policy.action,
                    "resource_type": policy.resource_type,
                    "effect": policy.effect,
                    "constraints": self._safe_constraints(policy.constraints_json),
                    "priority": policy.priority,
                }
                for policy in policy_rows
            ],
        }
        payload_json = _canonical(payload)
        checksum = _digest(payload_json)
        snapshot = await self._session.scalar(
            select(NodeConfigSnapshot).where(
                NodeConfigSnapshot.tenant_id == node.tenant_id,
                NodeConfigSnapshot.organization_id == node.organization_id,
                NodeConfigSnapshot.checksum == checksum,
            )
        )
        if snapshot is None:
            latest = await self._session.scalar(
                select(func.max(NodeConfigSnapshot.revision)).where(
                    NodeConfigSnapshot.tenant_id == node.tenant_id,
                    NodeConfigSnapshot.organization_id == node.organization_id,
                )
            )
            snapshot = NodeConfigSnapshot(
                tenant_id=node.tenant_id,
                organization_id=node.organization_id,
                revision=int(latest or 0) + 1,
                checksum=checksum,
                payload_json=payload_json,
                created_at=self._now(),
            )
            self._session.add(snapshot)
            await self._session.flush()
        node.desired_config_revision = snapshot.revision
        await self._session.flush()
        return {
            "revision": snapshot.revision,
            "checksum": snapshot.checksum,
            "configuration": json.loads(snapshot.payload_json),
        }

    @staticmethod
    def _safe_composition(payload_json: str) -> dict[str, object]:
        """Project only the approved declarative capability composition keys."""
        try:
            value = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        result: dict[str, object] = {}
        for key in ("skills", "tools", "knowledge"):
            items = value.get(key)
            if isinstance(items, list):
                result[key] = [str(item)[:128] for item in items[:64] if isinstance(item, str)]
        for key in ("model_policy", "default_risk", "capability_key", "source"):
            item = value.get(key)
            if isinstance(item, str):
                result[key] = item[:128]
        return result

    @staticmethod
    def _safe_constraints(payload_json: str) -> dict[str, object]:
        """Return a bounded declarative policy constraint map without executable values."""
        try:
            value = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        encoded = _canonical(value)
        if len(encoded.encode("utf-8")) > 8_000 or any(marker in encoded.lower() for marker in _SENSITIVE_MARKERS):
            return {}
        return value

    async def acknowledge_configuration(
        self,
        node: DepartmentNode,
        *,
        revision: int,
        status: str,
        error_code: str | None,
        error_summary: str,
    ) -> dict[str, object]:
        """Record one idempotent apply result while retaining last-known-good on failure."""
        if status not in {"applied", "failed"} or revision <= 0 or revision > node.desired_config_revision:
            raise GovernanceValidationError("Configuration acknowledgement is invalid")
        existing = await self._session.scalar(
            select(NodeConfigApplication).where(
                NodeConfigApplication.node_id == node.id,
                NodeConfigApplication.revision == revision,
            )
        )
        if existing is None:
            existing = NodeConfigApplication(
                tenant_id=node.tenant_id,
                node_id=node.id,
                revision=revision,
                status=status,
                error_code=(error_code or "")[:64] or None,
                error_summary=_safe_summary(error_summary),
                acknowledged_at=self._now(),
            )
            self._session.add(existing)
            if status == "applied":
                node.applied_config_revision = revision
            await self._session.flush()
        elif existing.status == "failed" and status == "applied":
            existing.status = "applied"
            existing.error_code = None
            existing.error_summary = ""
            existing.acknowledged_at = self._now()
            node.applied_config_revision = revision
            await self._session.flush()
        return {
            "node_id": node.id,
            "revision": existing.revision,
            "status": existing.status,
            "applied_config_revision": node.applied_config_revision,
            "error_code": existing.error_code,
            "error_summary": existing.error_summary,
        }

    async def query_diagnostics(
        self,
        subject: SubjectContext,
        *,
        start_at: datetime,
        end_at: datetime,
        organization_id: str | None = None,
        node_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        capability_version: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Project bounded redacted task facts under tenant, role, scope, and time filters."""
        self._require_role(
            subject,
            GovernedRole.ENTERPRISE_ADMIN,
            GovernedRole.DEPARTMENT_ADMIN,
            GovernedRole.AUDITOR,
        )
        start, end = _utc(start_at), _utc(end_at)
        if start >= end or end - start > timedelta(days=MAX_DIAGNOSTIC_DAYS):
            raise GovernanceValidationError("Diagnostic time range is invalid")
        if organization_id:
            await self._organization(subject.tenant_id, organization_id)
            self._authorize_organization(subject, organization_id)
        if node_id:
            node = await self._admin_node(subject, node_id)
            if organization_id and node.organization_id != organization_id:
                raise GovernanceValidationError("Diagnostic node scope is invalid")
            organization_id = node.organization_id
        statement = select(Task, TaskContextSnapshot).outerjoin(
            TaskContextSnapshot, TaskContextSnapshot.task_id == Task.id
        ).where(Task.tenant_id == subject.tenant_id, Task.created_at >= start, Task.created_at <= end)
        if GovernedRole.DEPARTMENT_ADMIN in subject.roles and GovernedRole.ENTERPRISE_ADMIN not in subject.roles:
            statement = statement.where(Task.organization_id.in_(subject.organization_ids))
        if organization_id:
            statement = statement.where(Task.organization_id == organization_id)
        if node_id:
            statement = statement.where(Task.node_id == node_id)
        if task_id:
            statement = statement.where(Task.id == task_id)
        if status:
            statement = statement.where(Task.status == status)
        rows = tuple((await self._session.execute(statement.order_by(Task.created_at.desc()).limit(MAX_DIAGNOSTIC_RECORDS + 1))).all())
        if len(rows) > MAX_DIAGNOSTIC_RECORDS:
            raise GovernanceValidationError("Diagnostic result is too large")
        result: list[dict[str, object]] = []
        for task, snapshot in rows:
            versions = self._capability_versions(snapshot.capability_snapshot_json if snapshot else "[]")
            if capability_version and capability_version not in versions:
                continue
            result.append(
                {
                    "task_id": task.id,
                    "organization_id": task.organization_id,
                    "node_id": task.node_id,
                    "status": task.status,
                    "task_type": task.task_type,
                    "capability_versions": versions,
                    "error_code": self._error_code(task.error_message),
                    "error_summary": _safe_summary(task.error_message),
                    "created_at": task.created_at,
                    "updated_at": task.updated_at,
                }
            )
        return tuple(result)

    @staticmethod
    def _capability_versions(payload_json: str) -> list[str]:
        """Extract only bounded capability version references from a task snapshot."""
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [str(item)[:128] for item in payload[:32] if isinstance(item, str)]

    @staticmethod
    def _error_code(error: str | None) -> str | None:
        """Derive a non-sensitive stable error category from task state."""
        if not error:
            return None
        lowered = error.lower()
        if "timeout" in lowered or "timed out" in lowered:
            return "timeout"
        if any(marker in lowered for marker in _SENSITIVE_MARKERS):
            return "redacted_error"
        return "task_failed"

    async def create_diagnostic_package(
        self,
        subject: SubjectContext,
        *,
        start_at: datetime,
        end_at: datetime,
        expires_in_minutes: int,
        organization_id: str | None = None,
        node_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        capability_version: str | None = None,
    ) -> dict[str, object]:
        """Persist one immutable expiring JSON manifest from the validated diagnostic query."""
        if not 5 <= expires_in_minutes <= 1_440:
            raise GovernanceValidationError("Diagnostic package expiry is invalid")
        records = await self.query_diagnostics(
            subject,
            start_at=start_at,
            end_at=end_at,
            organization_id=organization_id,
            node_id=node_id,
            task_id=task_id,
            status=status,
            capability_version=capability_version,
        )
        filters = {
            "start_at": _utc(start_at).isoformat(),
            "end_at": _utc(end_at).isoformat(),
            "organization_id": organization_id,
            "node_id": node_id,
            "task_id": task_id,
            "status": status,
            "capability_version": capability_version,
        }
        manifest = {
            "schema_version": "department-diagnostics.v1",
            "generated_at": self._now().isoformat(),
            "filters": filters,
            "records": [self._json_record(item) for item in records],
        }
        manifest_json = _canonical(manifest)
        if len(manifest_json.encode("utf-8")) > MAX_DIAGNOSTIC_BYTES:
            raise GovernanceValidationError("Diagnostic package is too large")
        item = DiagnosticPackage(
            tenant_id=subject.tenant_id,
            organization_id=organization_id,
            node_id=node_id,
            requested_by=subject.user_id,
            filters_json=_canonical(filters),
            manifest_json=manifest_json,
            checksum=_digest(manifest_json),
            record_count=len(records),
            expires_at=self._now() + timedelta(minutes=expires_in_minutes),
            created_at=self._now(),
        )
        self._session.add(item)
        await self._session.flush()
        await self._audit(
            tenant_id=subject.tenant_id,
            subject_id=subject.user_id,
            result_status="diagnostic_package_created",
            organization_id=organization_id,
            resource_type="diagnostic_package",
            resource_id=item.id,
            summary=f"Created redacted diagnostic package with {len(records)} records.",
        )
        return self._package_payload(item)

    @staticmethod
    def _json_record(item: dict[str, object]) -> dict[str, object]:
        """Convert diagnostic timestamps to an immutable JSON-safe representation."""
        return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in item.items()}

    def _package_payload(self, item: DiagnosticPackage) -> dict[str, object]:
        """Serialize package metadata without its stored manifest contents."""
        status = "expired" if _utc(item.expires_at) <= self._now() else item.status
        return {
            "id": item.id,
            "organization_id": item.organization_id,
            "node_id": item.node_id,
            "status": status,
            "checksum": item.checksum,
            "record_count": item.record_count,
            "expires_at": _utc(item.expires_at),
            "created_at": _utc(item.created_at),
            "downloaded_at": _utc(item.downloaded_at) if item.downloaded_at else None,
        }

    async def list_diagnostic_packages(self, subject: SubjectContext) -> tuple[dict[str, object], ...]:
        """List safe package metadata within the caller's tenant and organization scope."""
        self._require_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.DEPARTMENT_ADMIN, GovernedRole.AUDITOR)
        statement = select(DiagnosticPackage).where(DiagnosticPackage.tenant_id == subject.tenant_id)
        if GovernedRole.DEPARTMENT_ADMIN in subject.roles and GovernedRole.ENTERPRISE_ADMIN not in subject.roles:
            statement = statement.where(
                or_(
                    DiagnosticPackage.organization_id.in_(subject.organization_ids),
                    DiagnosticPackage.requested_by == subject.user_id,
                )
            )
        rows = tuple(await self._session.scalars(statement.order_by(DiagnosticPackage.created_at.desc()).limit(100)))
        return tuple(self._package_payload(row) for row in rows)

    async def download_diagnostic_package(self, subject: SubjectContext, package_id: str) -> str:
        """Return an unexpired redacted manifest with its canonical checksum and audit access."""
        self._require_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.DEPARTMENT_ADMIN, GovernedRole.AUDITOR)
        item = await self._session.get(DiagnosticPackage, package_id)
        if item is None or item.tenant_id != subject.tenant_id or _utc(item.expires_at) <= self._now():
            raise GovernanceValidationError("Diagnostic package is unavailable")
        if item.organization_id:
            self._authorize_organization(subject, item.organization_id)
        elif (
            GovernedRole.DEPARTMENT_ADMIN in subject.roles
            and GovernedRole.ENTERPRISE_ADMIN not in subject.roles
            and item.requested_by != subject.user_id
        ):
            raise GovernanceValidationError("Diagnostic package is unavailable")
        item.downloaded_at = self._now()
        await self._audit(
            tenant_id=subject.tenant_id,
            subject_id=subject.user_id,
            result_status="diagnostic_package_downloaded",
            organization_id=item.organization_id,
            resource_type="diagnostic_package",
            resource_id=item.id,
            summary="Downloaded redacted diagnostic package.",
        )
        await self._session.flush()
        return _canonical(
            {"checksum": item.checksum, "manifest": json.loads(item.manifest_json)}
        )

    async def create_operation(
        self,
        subject: SubjectContext,
        *,
        node_id: str,
        operation_type: str,
        target_task_id: str | None,
        idempotency_key: str,
        expires_in_minutes: int,
    ) -> dict[str, object]:
        """Create one whitelisted idempotent operation after role and task-scope checks."""
        self._require_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.DEPARTMENT_ADMIN)
        if operation_type not in _NODE_OPERATIONS or not idempotency_key or len(idempotency_key) > 128:
            raise GovernanceValidationError("Remote operation is invalid")
        if not 1 <= expires_in_minutes <= 60:
            raise GovernanceValidationError("Remote operation expiry is invalid")
        existing = await self._session.scalar(
            select(NodeRemoteOperation).where(
                NodeRemoteOperation.tenant_id == subject.tenant_id,
                NodeRemoteOperation.requested_by == subject.user_id,
                NodeRemoteOperation.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return self._operation_payload(existing)
        node = await self._admin_node(subject, node_id)
        if node.status != "active":
            raise GovernanceValidationError("Node is unavailable")
        if operation_type == "restart_agent" and GovernedRole.ENTERPRISE_ADMIN not in subject.roles:
            raise GovernanceValidationError("Agent restart requires enterprise authority")
        if operation_type == "stop_task":
            task = await self._session.get(Task, target_task_id or "")
            if (
                task is None
                or task.tenant_id != subject.tenant_id
                or task.organization_id != node.organization_id
                or task.status in _TERMINAL_TASK_STATUSES
            ):
                raise GovernanceValidationError("Task is unavailable")
        elif target_task_id is not None:
            raise GovernanceValidationError("Only stop-task accepts a task identity")
        item = NodeRemoteOperation(
            tenant_id=subject.tenant_id,
            organization_id=node.organization_id,
            node_id=node.id,
            requested_by=subject.user_id,
            operation_type=operation_type,
            target_task_id=target_task_id,
            idempotency_key=idempotency_key,
            expires_at=self._now() + timedelta(minutes=expires_in_minutes),
        )
        self._session.add(item)
        await self._session.flush()
        await self._audit(
            tenant_id=subject.tenant_id,
            subject_id=subject.user_id,
            result_status="node_operation_queued",
            organization_id=node.organization_id,
            resource_type="node_remote_operation",
            resource_id=item.id,
            task_id=target_task_id,
            summary=f"Queued whitelisted node operation {operation_type}.",
        )
        return self._operation_payload(item)

    async def list_operations(self, subject: SubjectContext) -> tuple[dict[str, object], ...]:
        """List safe operation ledger facts within administrator organization scope."""
        self._require_role(subject, GovernedRole.ENTERPRISE_ADMIN, GovernedRole.DEPARTMENT_ADMIN)
        statement = select(NodeRemoteOperation).where(NodeRemoteOperation.tenant_id == subject.tenant_id)
        if GovernedRole.ENTERPRISE_ADMIN not in subject.roles:
            statement = statement.where(NodeRemoteOperation.organization_id.in_(subject.organization_ids))
        rows = tuple(await self._session.scalars(statement.order_by(NodeRemoteOperation.created_at.desc()).limit(100)))
        return tuple(self._operation_payload(row) for row in rows)

    async def poll_operations(self, node: DepartmentNode) -> tuple[dict[str, object], ...]:
        """Deliver only this node's queued unexpired operations and expire old work first."""
        now = self._now()
        queued = tuple(
            await self._session.scalars(
                select(NodeRemoteOperation).where(
                    NodeRemoteOperation.tenant_id == node.tenant_id,
                    NodeRemoteOperation.node_id == node.id,
                    NodeRemoteOperation.status == "queued",
                ).order_by(NodeRemoteOperation.created_at).limit(20)
            )
        )
        delivered: list[dict[str, object]] = []
        for item in queued:
            if _utc(item.expires_at) <= now:
                item.status = "expired"
                item.completed_at = now
                continue
            item.status = "delivered"
            item.delivered_at = now
            delivered.append(self._operation_payload(item))
        await self._session.flush()
        return tuple(delivered)

    async def acknowledge_operation(
        self,
        node: DepartmentNode,
        *,
        operation_id: str,
        status: str,
        result_code: str,
        result_summary: str,
    ) -> dict[str, object]:
        """Complete one delivered operation idempotently with bounded node results."""
        item = await self._session.get(NodeRemoteOperation, operation_id)
        if item is None or item.tenant_id != node.tenant_id or item.node_id != node.id:
            raise GovernanceValidationError("Remote operation is unavailable")
        if item.status in _TERMINAL_OPERATION_STATUSES:
            return self._operation_payload(item)
        if item.status != "delivered" or status not in {"succeeded", "failed"}:
            raise GovernanceValidationError("Remote operation acknowledgement is invalid")
        item.status = status
        item.result_code = result_code.strip()[:64]
        item.result_summary = _safe_summary(result_summary)
        item.completed_at = self._now()
        if status == "succeeded":
            if item.operation_type == "pause_new_work":
                node.accepts_new_work = False
            elif item.operation_type == "resume_new_work":
                node.accepts_new_work = True
            elif item.operation_type == "stop_task" and item.target_task_id:
                task = await self._session.get(Task, item.target_task_id)
                if task is not None and task.status not in _TERMINAL_TASK_STATUSES:
                    task.status = "cancelled"
                    task.result_text = "任务已由受控运维停止。"
                    task.error_message = None
                    await TaskEventRepository(self._session).append(
                        task_id=task.id,
                        user_id=task.user_id,
                        event_type=TASK_EVENT_STATUS,
                        payload={"status": "cancelled", "reason": "controlled_node_operation"},
                    )
        await self._audit(
            tenant_id=node.tenant_id,
            subject_id=node.id,
            result_status=f"node_operation_{status}",
            organization_id=node.organization_id,
            resource_type="node_remote_operation",
            resource_id=item.id,
            task_id=item.target_task_id,
            summary=f"Node acknowledged {item.operation_type}: {item.result_code}.",
        )
        await self._session.flush()
        return self._operation_payload(item)

    def _operation_payload(self, item: NodeRemoteOperation) -> dict[str, object]:
        """Serialize allowlisted operation facts without free-form command parameters."""
        status = item.status
        if status == "queued" and _utc(item.expires_at) <= self._now():
            status = "expired"
        return {
            "id": item.id,
            "organization_id": item.organization_id,
            "node_id": item.node_id,
            "operation_type": item.operation_type,
            "target_task_id": item.target_task_id,
            "status": status,
            "expires_at": _utc(item.expires_at),
            "delivered_at": _utc(item.delivered_at) if item.delivered_at else None,
            "completed_at": _utc(item.completed_at) if item.completed_at else None,
            "result_code": item.result_code,
            "result_summary": item.result_summary,
            "created_at": _utc(item.created_at),
        }
