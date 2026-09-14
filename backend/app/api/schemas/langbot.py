from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LangBotConversation(BaseModel):
    """表示 处理 lang bot conversation 的后端数据结构或服务对象。"""

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)


class LangBotSender(BaseModel):
    """表示 处理 lang bot sender 的后端数据结构或服务对象。"""

    id: str = Field(min_length=1)


class LangBotMessage(BaseModel):
    """表示 处理 lang bot message 的后端数据结构或服务对象。"""

    type: Literal["text"]
    text: str = Field(min_length=1)


class LangBotWebhookRequest(BaseModel):
    """表示 处理 lang bot webhook request 的后端数据结构或服务对象。"""

    message_id: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    conversation: LangBotConversation
    sender: LangBotSender
    message: LangBotMessage
