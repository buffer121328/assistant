from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from domain.models import AccountConnection


class AccountConnectionCreateRequest(BaseModel):
    """表示 处理 account connection create request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1, max_length=36)
    provider: Literal["smtp", "caldav", "browser"]
    display_name: str = Field(min_length=1, max_length=255)
    credentials: dict[str, str]


class AccountConnectionActorRequest(BaseModel):
    """表示 处理 account connection actor request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1, max_length=36)


class AccountConnectionResponse(BaseModel):
    """表示 处理 account connection response 的后端数据结构或服务对象。"""

    connection_id: str
    user_id: str
    provider: str
    display_name: str
    status: str
    last_checked_at: datetime | None
    last_error_code: str | None


class AccountConnectionListResponse(BaseModel):
    """表示 处理 account connection list response 的后端数据结构或服务对象。"""

    items: list[AccountConnectionResponse]


def account_connection_response(item: AccountConnection) -> AccountConnectionResponse:
    """处理 account connection response。

    Args:
        item: item 参数。
    """
    return AccountConnectionResponse(
        connection_id=item.id,
        user_id=item.user_id,
        provider=item.provider,
        display_name=item.display_name,
        status=item.status,
        last_checked_at=item.last_checked_at,
        last_error_code=item.last_error_code,
    )
