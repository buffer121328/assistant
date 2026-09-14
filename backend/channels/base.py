from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class IncomingMessage:
    """定义当前组件的职责和边界。"""

    platform: str  # platform 对应的数据字段。
    adapter: str  # adapter 对应的数据字段。
    sender_id: str  # sender_id 对应的数据字段。
    conversation_id: str  # conversation_id 对应的数据字段。
    conversation_type: str  # conversation_type 对应的数据字段。
    text: str  # text 对应的数据字段。
    message_id: str  # message_id 对应的数据字段。

    def as_response(self) -> dict[str, str]:
        """执行当前组件定义的业务处理逻辑。"""
        return {
            "platform": self.platform,
            "adapter": self.adapter,
            "sender_id": self.sender_id,
            "conversation_id": self.conversation_id,
            "conversation_type": self.conversation_type,
            "text": self.text,
            "message_id": self.message_id,
        }


@dataclass(frozen=True)
class OutgoingMessage:
    """定义当前组件的职责和边界。"""

    message_id: str  # message_id 对应的数据字段。
    conversation_id: str  # conversation_id 对应的数据字段。
    text: str  # text 对应的数据字段。
    metadata: dict[str, Any] = field(default_factory=dict)  # metadata 对应的数据字段。


class ChannelAdapter(Protocol):
    """定义当前组件的接口契约。"""

    async def ingest(self, payload: Any) -> IncomingMessage:
        """Normalize external payload data for the shared task service.

        Args:
            payload: 当前操作的结构化载荷。
        """

    async def deliver(self, message: OutgoingMessage) -> None:
        """Deliver one bounded idempotent outbound message.

        Args:
            message: 用于执行当前操作的 message 参数。
        """
