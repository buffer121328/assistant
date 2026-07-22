from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReminderCreateRequest(BaseModel):
    """表示 处理 reminder create request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=10_000)
    due_at: datetime
    channel: Literal["desktop", "langbot"]


class ReminderActorRequest(BaseModel):
    """表示 处理 reminder actor request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1, max_length=36)


class ReminderResponse(BaseModel):
    """表示 处理 reminder response 的后端数据结构或服务对象。"""

    reminder_id: str
    user_id: str
    title: str
    message: str
    due_at: datetime
    channel: str
    status: str
    cancelled_at: datetime | None
    delivery_status: str | None = None
    last_error_code: str | None = None


class ReminderListResponse(BaseModel):
    """表示 处理 reminder list response 的后端数据结构或服务对象。"""

    items: list[ReminderResponse]


class DesktopNotificationResponse(BaseModel):
    """表示 处理 desktop notification response 的后端数据结构或服务对象。"""

    outbox_id: str
    reminder_id: str
    title: str
    message: str
    due_at: datetime


class DesktopNotificationListResponse(BaseModel):
    """表示 处理 desktop notification list response 的后端数据结构或服务对象。"""

    items: list[DesktopNotificationResponse]
