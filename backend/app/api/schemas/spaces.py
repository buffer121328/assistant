from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from domain.models import Space, SpaceMembership


class SpaceCreateRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4_000)
    organization_id: str | None = Field(default=None, min_length=1, max_length=36)


class SpaceActorRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1, max_length=36)


class SpaceMembershipCreateRequest(BaseModel):
    """定义当前接口使用的数据模型。"""

    user_id: str = Field(min_length=1, max_length=36)
    member_user_id: str = Field(min_length=1, max_length=36)
    role: Literal["editor", "viewer"]


class SpaceMembershipResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    membership_id: str
    space_id: str
    user_id: str
    role: Literal["owner", "editor", "viewer"]
    status: Literal["active", "revoked"]
    granted_by: str
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SpaceResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    space_id: str
    tenant_id: str
    organization_id: str | None
    name: str
    description: str
    status: Literal["active", "archived"]
    created_by: str
    role: Literal["owner", "editor", "viewer"]
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SpaceListResponse(BaseModel):
    """定义当前接口使用的数据模型。"""

    items: list[SpaceResponse]


class SpaceDetailResponse(SpaceResponse):
    """定义当前接口使用的数据模型。"""

    members: list[SpaceMembershipResponse]


def space_response(item: Space, membership: SpaceMembership) -> SpaceResponse:
    """Convert persisted Space data to the bounded API contract.

    Args:
        item: 用于执行当前操作的 item 参数。
        membership: 用于执行当前操作的 membership 参数。
    """
    return SpaceResponse(
        space_id=item.id,
        tenant_id=item.tenant_id,
        organization_id=item.organization_id,
        name=item.name,
        description=item.description,
        status=item.status,  # type: ignore[arg-type]
        created_by=item.created_by,
        role=membership.role,  # type: ignore[arg-type]
        archived_at=item.archived_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def space_membership_response(item: SpaceMembership) -> SpaceMembershipResponse:
    """Convert one membership without exposing unrelated user information.

    Args:
        item: 用于执行当前操作的 item 参数。
    """
    return SpaceMembershipResponse(
        membership_id=item.id,
        space_id=item.space_id,
        user_id=item.user_id,
        role=item.role,  # type: ignore[arg-type]
        status=item.status,  # type: ignore[arg-type]
        granted_by=item.granted_by,
        revoked_at=item.revoked_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
