from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.enterprise_runtime import (
    GovernanceAuditInput,
    GovernanceAuditService,
    require_ai_department_administrator,
    require_admin_role,
)
from domain.models import CapabilityRequest, Organization, Task, User
from domain.policies.enterprise import GovernedRole, GovernanceValidationError, SubjectContext

CAPABILITY_REQUEST_STATUSES = (
    "pending", "communicating", "configuring", "testing",
    "pending_approval", "published", "rejected", "closed",
)

_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"communicating", "rejected", "closed"}),
    "communicating": frozenset({"configuring", "rejected", "closed"}),
    "configuring": frozenset({"testing", "rejected", "closed"}),
    "testing": frozenset({"configuring", "pending_approval", "rejected", "closed"}),
    "pending_approval": frozenset({"configuring", "published", "rejected", "closed"}),
    "published": frozenset({"closed"}),
    "rejected": frozenset({"closed"}),
    "closed": frozenset(),
}


class CapabilityRequestService:
    """Own capability request identity, visibility, lifecycle, and audit boundaries."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind request operations to the caller's transaction."""
        self._session = session

    async def create(
        self,
        subject: SubjectContext,
        *,
        title: str,
        description: str,
        related_task_id: str | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Create or replay one owner-scoped request and append audit evidence."""
        if not idempotency_key or len(idempotency_key) > 128:
            raise GovernanceValidationError("Capability request idempotency key is required")
        existing = await self._session.scalar(
            select(CapabilityRequest).where(
                CapabilityRequest.tenant_id == subject.tenant_id,
                CapabilityRequest.requester_id == subject.user_id,
                CapabilityRequest.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return await self._serialize(existing, admin=False)
        normalized_title = title.strip()
        normalized_description = description.strip()
        if not normalized_title or len(normalized_title) > 160:
            raise GovernanceValidationError("Capability request title is invalid")
        if not normalized_description or len(normalized_description) > 2_000:
            raise GovernanceValidationError("Capability request description is invalid")
        if not subject.organization_ids:
            raise GovernanceValidationError("Capability request organization is unavailable")
        organization = await self._session.get(Organization, subject.organization_ids[0])
        if organization is None or organization.tenant_id != subject.tenant_id or organization.status != "active":
            raise GovernanceValidationError("Capability request organization is unavailable")
        if related_task_id:
            task = await self._session.get(Task, related_task_id)
            if task is None or task.tenant_id != subject.tenant_id or task.user_id != subject.user_id:
                raise GovernanceValidationError("Capability request task is unavailable")
        row = CapabilityRequest(
            tenant_id=subject.tenant_id,
            requester_id=subject.user_id,
            organization_id=organization.id,
            related_task_id=related_task_id,
            title=normalized_title,
            description=normalized_description,
            status="pending",
            idempotency_key=idempotency_key,
            updated_by=subject.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._audit(subject, row, result_status="request_created")
        return await self._serialize(row, admin=False)

    async def list_personal(self, subject: SubjectContext) -> tuple[dict[str, object], ...]:
        """Return only requests owned by the authenticated subject."""
        rows = tuple(await self._session.scalars(
            select(CapabilityRequest).where(
                CapabilityRequest.tenant_id == subject.tenant_id,
                CapabilityRequest.requester_id == subject.user_id,
            ).order_by(CapabilityRequest.created_at.desc())
        ))
        return tuple([await self._serialize(row, admin=False) for row in rows])

    async def list_admin(
        self, subject: SubjectContext, *, status: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Return the tenant queue to publishers or a verified AI department lead."""
        if not set(subject.roles).intersection(
            {GovernedRole.ENTERPRISE_ADMIN, GovernedRole.CAPABILITY_PUBLISHER}
        ):
            require_admin_role(subject, GovernedRole.DEPARTMENT_ADMIN)
            await require_ai_department_administrator(self._session, subject)
        if status is not None and status not in CAPABILITY_REQUEST_STATUSES:
            raise GovernanceValidationError("Capability request status is invalid")
        statement = select(CapabilityRequest).where(CapabilityRequest.tenant_id == subject.tenant_id)
        if status is not None:
            statement = statement.where(CapabilityRequest.status == status)
        rows = tuple(await self._session.scalars(statement.order_by(CapabilityRequest.created_at.desc())))
        return tuple([await self._serialize(row, admin=True) for row in rows])

    async def update_status(
        self, subject: SubjectContext, *, request_id: str, status: str,
    ) -> dict[str, object]:
        """Apply one allowed lifecycle transition and audit the acting subject."""
        if status == "published":
            await require_ai_department_administrator(self._session, subject)
        else:
            require_admin_role(
                subject,
                GovernedRole.ENTERPRISE_ADMIN,
                GovernedRole.CAPABILITY_PUBLISHER,
            )
        row = await self._session.get(CapabilityRequest, request_id)
        if row is None or row.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Capability request is unavailable")
        if status not in _TRANSITIONS.get(row.status, frozenset()):
            raise GovernanceValidationError("Capability request transition is invalid")
        row.status = status
        row.updated_by = subject.user_id
        await self._session.flush()
        await self._audit(subject, row, result_status="status_changed")
        return await self._serialize(row, admin=True)

    async def _serialize(self, row: CapabilityRequest, *, admin: bool) -> dict[str, object]:
        """Build bounded employee or IT presentation facts without internal payloads."""
        organization = await self._session.get(Organization, row.organization_id)
        payload: dict[str, object] = {
            "id": row.id,
            "title": row.title,
            "description": row.description,
            "status": row.status,
            "organization_name": organization.name if organization is not None else "",
            "related_task_id": row.related_task_id,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
        if admin:
            requester = await self._session.get(User, row.requester_id)
            payload["requester_display_name"] = requester.display_name if requester is not None else ""
        return payload

    async def _audit(
        self, subject: SubjectContext, row: CapabilityRequest, *, result_status: str,
    ) -> None:
        """Append a bounded governance fact within the request transaction."""
        await GovernanceAuditService(self._session).record(
            GovernanceAuditInput(
                tenant_id=subject.tenant_id,
                subject_id=subject.user_id,
                organization_id=row.organization_id,
                task_id=row.related_task_id,
                resource_type="capability_request",
                resource_id=row.id,
                policy_decision="ALLOW",
                result_status=result_status,
                summary=f"Capability request {result_status}",
            )
        )
