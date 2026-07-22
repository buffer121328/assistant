from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, Field

from domain.models import Conversation, ConversationMessage


class ConversationCreateRequest(BaseModel):
    """表示 处理 conversation create request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=255)


class ConversationActorRequest(BaseModel):
    """表示 处理 conversation actor request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)


class ConversationResponse(BaseModel):
    """表示 处理 conversation response 的后端数据结构或服务对象。"""

    conversation_id: str
    user_id: str
    title: str
    channel: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    """表示 处理 conversation list response 的后端数据结构或服务对象。"""

    items: list[ConversationResponse]


class ConversationMessageResponse(BaseModel):
    """表示 处理 conversation message response 的后端数据结构或服务对象。"""

    message_id: str
    conversation_id: str
    task_id: str | None
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationMessageListResponse(BaseModel):
    """表示 处理 conversation message list response 的后端数据结构或服务对象。"""

    items: list[ConversationMessageResponse]
    compacted: bool = False
    summary_updated_at: datetime | None = None
    summary_version: str | None = None


def conversation_response(item: Conversation) -> ConversationResponse:
    """处理 conversation response。

    Args:
        item: item 参数。
    """
    return ConversationResponse(
        conversation_id=item.id,
        user_id=item.user_id,
        title=item.title,
        channel=item.channel,
        archived_at=item.archived_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def conversation_message_response(
    item: ConversationMessage,
) -> ConversationMessageResponse:
    """处理 conversation message response。

    Args:
        item: item 参数。
    """
    return ConversationMessageResponse(
        message_id=item.id,
        conversation_id=item.conversation_id,
        task_id=item.task_id,
        role=cast(Literal["user", "assistant"], item.role),
        content=item.content,
        created_at=item.created_at,
    )
