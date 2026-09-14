from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import (
    Organization,
    OrganizationMembership,
    Space,
    SpaceMembership,
    User,
    utc_now,
)
from domain.policies.redaction import sanitize_text


class SpaceError(RuntimeError):
    """定义当前组件可安全处理的错误类型。"""

    def __init__(self, code: str, status_code: int = 400) -> None:
        """Initialize one safe Space boundary error.

        Args:
            code: 可安全返回给调用方的错误码。
            status_code: 可安全返回给调用方的 HTTP 状态码。
        """
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class SpaceService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the service to one database transaction boundary.

        Args:
            session: 当前数据库异步会话。
        """
        self.session = session

    async def create(
        self,
        *,
        user_id: str,
        name: str,
        description: str = "",
        organization_id: str | None = None,
        commit: bool = True,
    ) -> Space:
        """Create a tenant Space and immutable owner membership atomically.

        Args:
            user_id: 目标用户 ID。
            name: 目标对象或能力的名称。
            description: 用于执行当前操作的 description 参数。
            organization_id: 用于执行当前操作的 organization id 参数。
            commit: 用于执行当前操作的 commit 参数。
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise SpaceError("space_user_not_found", 404)
        if organization_id is not None:
            await self._require_organization_member(
                user=user, organization_id=organization_id
            )
        safe_name = " ".join(sanitize_text(name).strip().split())[:255]
        if not safe_name:
            raise SpaceError("space_name_required", 422)
        space = Space(
            tenant_id=user.tenant_id,
            organization_id=organization_id,
            name=safe_name,
            description=sanitize_text(description).strip()[:4_000],
            status="active",
            created_by=user.id,
        )
        self.session.add(space)
        await self.session.flush()
        self.session.add(
            SpaceMembership(
                tenant_id=user.tenant_id,
                space_id=space.id,
                user_id=user.id,
                role="owner",
                status="active",
                granted_by=user.id,
            )
        )
        await self.session.flush()
        if commit:
            await self.session.commit()
            await self.session.refresh(space)
        return space

    async def list_accessible(self, user_id: str, *, limit: int = 100) -> list[Space]:
        """List active Spaces having an active exact-user membership.

        Args:
            user_id: 目标用户 ID。
            limit: 返回结果的最大数量。
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise SpaceError("space_user_not_found", 404)
        items = await self.session.scalars(
            select(Space)
            .join(SpaceMembership, SpaceMembership.space_id == Space.id)
            .where(
                Space.tenant_id == user.tenant_id,
                Space.status == "active",
                SpaceMembership.tenant_id == user.tenant_id,
                SpaceMembership.user_id == user.id,
                SpaceMembership.status == "active",
            )
            .order_by(Space.updated_at.desc(), Space.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(items)

    async def list_owned_active(self, user_id: str, *, limit: int = 100) -> list[Space]:
        """List only active personal Workspaces created by the current user.

        The desktop personal workbench deliberately does not treat membership in
        another user's Space as a filing destination. Department and future
        collaboration flows can continue using ``list_accessible``.

        Args:
            user_id: 当前用户 ID。
            limit: 返回结果的最大数量。
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise SpaceError("space_user_not_found", 404)
        items = await self.session.scalars(
            select(Space)
            .where(
                Space.tenant_id == user.tenant_id,
                Space.created_by == user.id,
                Space.status == "active",
            )
            .order_by(Space.updated_at.desc(), Space.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(items)

    async def require_owned_active(self, *, space_id: str, user_id: str) -> Space:
        """Resolve one active personal Workspace created by the current user.

        Args:
            space_id: 当前工作区 ID。
            user_id: 当前用户 ID。
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise SpaceError("space_not_found", 404)
        item = await self.session.scalar(
            select(Space).where(
                Space.id == space_id,
                Space.tenant_id == user.tenant_id,
                Space.created_by == user.id,
                Space.status == "active",
            )
        )
        if item is None:
            raise SpaceError("space_not_found", 404)
        return item

    async def get_accessible(
        self, *, space_id: str, user_id: str, active_only: bool = True
    ) -> Space:
        """Resolve one Space only through active same-tenant membership.

        Args:
            space_id: 用于执行当前操作的 space id 参数。
            user_id: 目标用户 ID。
            active_only: 用于执行当前操作的 active only 参数。
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise SpaceError("space_not_found", 404)
        conditions = [
            Space.id == space_id,
            Space.tenant_id == user.tenant_id,
            SpaceMembership.space_id == Space.id,
            SpaceMembership.tenant_id == user.tenant_id,
            SpaceMembership.user_id == user_id,
            SpaceMembership.status == "active",
        ]
        if active_only:
            conditions.append(Space.status == "active")
        item = await self.session.scalar(
            select(Space).join(SpaceMembership).where(*conditions)
        )
        if item is None:
            raise SpaceError("space_not_found", 404)
        return item

    async def get_members(
        self, *, space_id: str, user_id: str
    ) -> list[SpaceMembership]:
        """List memberships only after proving caller access to the Space.

        Args:
            space_id: 用于执行当前操作的 space id 参数。
            user_id: 目标用户 ID。
        """
        await self.get_accessible(space_id=space_id, user_id=user_id)
        items = await self.session.scalars(
            select(SpaceMembership)
            .where(SpaceMembership.space_id == space_id)
            .order_by(SpaceMembership.created_at, SpaceMembership.id)
        )
        return list(items)

    async def get_membership(
        self, *, space_id: str, user_id: str
    ) -> SpaceMembership:
        """Return an active membership or a safe not-found error.

        Args:
            space_id: 用于执行当前操作的 space id 参数。
            user_id: 目标用户 ID。
        """
        item = await self.session.scalar(
            select(SpaceMembership).where(
                SpaceMembership.space_id == space_id,
                SpaceMembership.user_id == user_id,
                SpaceMembership.status == "active",
            )
        )
        if item is None:
            raise SpaceError("space_not_found", 404)
        return item

    async def require_edit_access(self, space_id: str, user_id: str) -> Space:
        """Require active owner/editor membership for binding or continuing work.

        Args:
            space_id: 用于执行当前操作的 space id 参数。
            user_id: 目标用户 ID。
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise SpaceError("space_not_found", 404)
        item = await self.session.scalar(
            select(Space)
            .join(SpaceMembership, SpaceMembership.space_id == Space.id)
            .where(
                Space.id == space_id,
                Space.tenant_id == user.tenant_id,
                Space.status == "active",
                SpaceMembership.tenant_id == user.tenant_id,
                SpaceMembership.user_id == user_id,
                SpaceMembership.status == "active",
            )
        )
        if item is None:
            raise SpaceError("space_not_found", 404)
        membership = await self.get_membership(space_id=space_id, user_id=user_id)
        if membership.role not in {"owner", "editor"}:
            raise SpaceError("space_edit_forbidden", 403)
        return item

    async def add_member(
        self,
        *,
        space_id: str,
        actor_user_id: str,
        member_user_id: str,
        role: str,
    ) -> SpaceMembership:
        """Add or reactivate a non-owner member under owner authorization.

        Args:
            space_id: 用于执行当前操作的 space id 参数。
            actor_user_id: 发起当前操作的用户 ID。
            member_user_id: 用于执行当前操作的 member user id 参数。
            role: 用于执行当前操作的 role 参数。
        """
        if role not in {"editor", "viewer"}:
            raise SpaceError("space_member_role_invalid", 422)
        space = await self._require_owner(space_id=space_id, user_id=actor_user_id)
        member = await self.session.get(User, member_user_id)
        if member is None:
            raise SpaceError("space_member_not_found", 404)
        if member.tenant_id != space.tenant_id:
            raise SpaceError("space_member_tenant_mismatch", 422)
        if space.organization_id is not None:
            await self._require_organization_member(
                user=member, organization_id=space.organization_id
            )
        membership = await self.session.scalar(
            select(SpaceMembership).where(
                SpaceMembership.space_id == space.id,
                SpaceMembership.user_id == member.id,
            )
        )
        if membership is None:
            membership = SpaceMembership(
                tenant_id=space.tenant_id,
                space_id=space.id,
                user_id=member.id,
                role=role,
                status="active",
                granted_by=actor_user_id,
            )
            self.session.add(membership)
        else:
            if membership.role == "owner":
                raise SpaceError("space_owner_membership_immutable", 409)
            membership.role = role
            membership.status = "active"
            membership.granted_by = actor_user_id
            membership.revoked_at = None
        space.updated_at = utc_now()
        await self.session.commit()
        await self.session.refresh(membership)
        return membership

    async def revoke_member(
        self,
        *,
        space_id: str,
        actor_user_id: str,
        member_user_id: str,
    ) -> SpaceMembership:
        """Revoke a non-owner membership without deleting private user data.

        Args:
            space_id: 用于执行当前操作的 space id 参数。
            actor_user_id: 发起当前操作的用户 ID。
            member_user_id: 用于执行当前操作的 member user id 参数。
        """
        space = await self._require_owner(space_id=space_id, user_id=actor_user_id)
        membership = await self.session.scalar(
            select(SpaceMembership).where(
                SpaceMembership.space_id == space.id,
                SpaceMembership.user_id == member_user_id,
            )
        )
        if membership is None:
            raise SpaceError("space_member_not_found", 404)
        if membership.role == "owner":
            raise SpaceError("space_owner_membership_immutable", 409)
        membership.status = "revoked"
        membership.revoked_at = membership.revoked_at or utc_now()
        space.updated_at = utc_now()
        await self.session.commit()
        await self.session.refresh(membership)
        return membership

    async def archive(self, *, space_id: str, user_id: str) -> Space:
        """Archive a Space under immutable owner membership authorization.

        Args:
            space_id: 用于执行当前操作的 space id 参数。
            user_id: 目标用户 ID。
        """
        space = await self._require_owner(
            space_id=space_id, user_id=user_id, active_only=False
        )
        space.status = "archived"
        space.archived_at = space.archived_at or utc_now()
        await self.session.commit()
        await self.session.refresh(space)
        return space

    async def _require_owner(
        self, *, space_id: str, user_id: str, active_only: bool = True
    ) -> Space:
        """Resolve an exact owner membership for management operations.

        Args:
            space_id: 用于执行当前操作的 space id 参数。
            user_id: 目标用户 ID。
            active_only: 用于执行当前操作的 active only 参数。
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise SpaceError("space_not_found", 404)
        conditions = [
            Space.id == space_id,
            Space.tenant_id == user.tenant_id,
            SpaceMembership.space_id == Space.id,
            SpaceMembership.tenant_id == user.tenant_id,
            SpaceMembership.user_id == user_id,
            SpaceMembership.status == "active",
        ]
        if active_only:
            conditions.append(Space.status == "active")
        row = await self.session.execute(
            select(Space, SpaceMembership).join(SpaceMembership).where(*conditions)
        )
        pair = row.first()
        if pair is None:
            raise SpaceError("space_not_found", 404)
        space, membership = pair
        if membership.role != "owner":
            raise SpaceError("space_owner_required", 403)
        return space

    async def _require_organization_member(
        self, *, user: User, organization_id: str
    ) -> Organization:
        """Validate active organization and exact-user membership in one tenant.

        Args:
            user: 用于执行当前操作的 user 参数。
            organization_id: 用于执行当前操作的 organization id 参数。
        """
        organization = await self.session.scalar(
            select(Organization).where(
                Organization.id == organization_id,
                Organization.tenant_id == user.tenant_id,
                Organization.status == "active",
            )
        )
        if organization is None:
            raise SpaceError("space_organization_not_found", 404)
        membership = await self.session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.tenant_id == user.tenant_id,
                OrganizationMembership.organization_id == organization.id,
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.status == "active",
            )
        )
        if membership is None:
            raise SpaceError("space_organization_membership_required", 403)
        return organization
