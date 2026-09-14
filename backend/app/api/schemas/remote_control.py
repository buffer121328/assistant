from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel

from domain.models import ProcessedMessage


class RemoteControlBridgeResponseTarget(BaseModel):
    """表示 处理 remote control bridge response target 的后端数据结构或服务对象。"""

    adapter: str
    conversation_id: str
    conversation_type: str


class RemoteControlBridgeSessionResponse(BaseModel):
    """表示 处理 remote control bridge session response 的后端数据结构或服务对象。"""

    bridge_id: str
    platform: str
    message_id: str
    adapter: str | None
    sender_id: str | None
    conversation_id: str | None
    conversation_type: str | None
    message_text: str | None
    intent_outcome: str | None
    reason: str
    task_id: str | None
    task_status: str | None
    response_target: RemoteControlBridgeResponseTarget | None
    delivery_status: str | None
    delivery_attempt_count: int
    delivery_error_summary: str | None
    delivery_result_json: str | None
    created_at: datetime
    updated_at: datetime


class RemoteControlBridgeSessionListResponse(BaseModel):
    """表示 处理 remote control bridge session list response 的后端数据结构或服务对象。"""

    items: list[RemoteControlBridgeSessionResponse]


class RemoteControlBridgeReplayResponse(BaseModel):
    """表示 处理 remote control bridge replay response 的后端数据结构或服务对象。"""

    dispatch_status: str
    message: str
    session: RemoteControlBridgeSessionResponse


def remote_control_bridge_response(
    item: ProcessedMessage,
    *,
    task_status: str | None = None,
) -> RemoteControlBridgeSessionResponse:
    """处理 remote control bridge response。

    Args:
        item: item 参数。
        task_status: task_status 参数。
    """
    response_target: RemoteControlBridgeResponseTarget | None = None
    if item.response_target:
        try:
            target = json.loads(item.response_target)
        except json.JSONDecodeError:
            target = None
        if isinstance(target, dict):
            adapter = target.get("adapter")
            conversation_id = target.get("conversation_id")
            conversation_type = target.get("conversation_type")
            if (
                isinstance(adapter, str)
                and isinstance(conversation_id, str)
                and isinstance(conversation_type, str)
            ):
                response_target = RemoteControlBridgeResponseTarget(
                    adapter=adapter,
                    conversation_id=conversation_id,
                    conversation_type=conversation_type,
                )

    return RemoteControlBridgeSessionResponse(
        bridge_id=item.id,
        platform=item.platform,
        message_id=item.message_id,
        adapter=item.adapter,
        sender_id=item.sender_id,
        conversation_id=item.chat_id,
        conversation_type=item.conversation_type,
        message_text=item.message_text,
        intent_outcome=item.intent_outcome,
        reason=item.reason,
        task_id=item.task_id,
        task_status=task_status,
        response_target=response_target,
        delivery_status=item.delivery_status,
        delivery_attempt_count=item.delivery_attempt_count,
        delivery_error_summary=item.delivery_error_summary,
        delivery_result_json=item.delivery_result_json,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
