from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.support.commands import parse_task_type
from infrastructure.settings.config import Settings
from session.conversations import ConversationService
from app.support.errors import AppError
from domain.models import Task
from infrastructure.repositories import MessageRepository, ProcessedMessageCreate
from app.api.schemas import LangBotWebhookRequest
from tasks.lifecycle import TaskService, TaskServiceError

from channels.langbot.intent import (
    ALL_COMMAND_TASK_TYPES,
    classify_langbot_intent,
)

PLATFORM = "langbot"
REASON_TASK_CREATED = "task_created"
REASON_DUPLICATE = "duplicate_message"
REASON_UNKNOWN_COMMAND = "unknown_command"
REASON_UNBOUND_USER = "unbound_user"


@dataclass(frozen=True)
class NormalizedLangBotMessage:
    """表示 处理 normalized lang bot message 的后端数据结构或服务对象。"""

    platform: str
    adapter: str
    sender_id: str
    conversation_id: str
    conversation_type: str
    text: str
    message_id: str

    def as_response(self) -> dict[str, str]:
        """处理 as response。"""
        return {
            "platform": self.platform,
            "adapter": self.adapter,
            "sender_id": self.sender_id,
            "conversation_id": self.conversation_id,
            "conversation_type": self.conversation_type,
            "text": self.text,
            "message_id": self.message_id,
        }


def verify_langbot_secret(*, headers: Any, settings: Settings) -> None:
    """处理 verify langbot secret。

    Args:
        headers: headers 参数。
        settings: settings 参数。
    """
    secret = headers.get("x-langbot-secret")
    if secret is None:
        secret = headers.get("X-LangBot-Secret")
    if str(secret or "") != settings.langbot_webhook_secret:
        raise AppError(
            code="langbot_invalid_secret",
            message="Invalid LangBot webhook secret",
            status_code=401,
        )


class LangBotResultClient:
    """表示 处理 lang bot result client 的后端数据结构或服务对象。"""

    def __init__(self, settings: Settings) -> None:
        """初始化对象实例。

        Args:
            settings: settings 参数。
        """
        self.settings = settings

    async def send_message(
        self,
        *,
        adapter: str,
        conversation_id: str,
        conversation_type: str,
        text: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """处理 send message。

        Args:
            adapter: adapter 参数。
            conversation_id: conversation_id 参数。
            conversation_type: conversation_type 参数。
            text: text 参数。
            idempotency_key: idempotency_key 参数。
        """
        payload = {
            "adapter": adapter,
            "conversation_id": conversation_id,
            "conversation_type": conversation_type,
            "text": text,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.langbot_api_key}",
        }
        if idempotency_key:
            bounded_key = idempotency_key.strip()[:128]
            if bounded_key:
                payload["idempotency_key"] = bounded_key
                headers["Idempotency-Key"] = bounded_key

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.langbot_send_timeout_seconds,
            ) as client:
                response = await client.post(
                    self.settings.langbot_api_base_url,
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise RuntimeError("LangBot result push timed out") from exc
        except httpx.TransportError as exc:
            raise RuntimeError(str(exc)) from exc

        if response.status_code >= 400:
            raise RuntimeError(response.text)

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("LangBot result push returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("LangBot result push returned invalid response shape")
        return data


async def handle_langbot_webhook(
    *,
    payload: LangBotWebhookRequest,
    headers: Any,
    session: AsyncSession,
    settings: Settings,
    task_handoff: Callable[[str], bool] | None = None,
) -> dict[str, object]:
    """处理 langbot webhook。

    Args:
        payload: payload 参数。
        headers: headers 参数。
        session: session 参数。
        settings: settings 参数。
        task_handoff: task_handoff 参数。
    """
    verify_langbot_secret(headers=headers, settings=settings)
    message = normalize_message(payload)
    normalized_text = message.text.strip()
    repository = MessageRepository(session)

    duplicate = await repository.get_processed_message(
        platform=PLATFORM,
        adapter=message.adapter,
        message_id=message.message_id,
    )
    if duplicate is not None:
        return ack(REASON_DUPLICATE)

    task_type = parse_task_type(message.text)
    if not normalized_text or normalized_text == "/":
        return await record_no_task_ack(
            session=session,
            repository=repository,
            message=message,
            reason=REASON_UNKNOWN_COMMAND,
            intent_outcome=None,
        )

    user_id = await repository.get_user_id_by_platform_account(
        platform=PLATFORM,
        platform_user_id=platform_sender_key(
            adapter=message.adapter,
            sender_id=message.sender_id,
        ),
    )
    if user_id is None:
        return await record_no_task_ack(
            session=session,
            repository=repository,
            message=message,
            reason=REASON_UNBOUND_USER,
            intent_outcome=None,
        )

    intent_outcome: str | None = task_type
    if task_type not in ALL_COMMAND_TASK_TYPES:
        intent = await classify_langbot_intent(message.text, settings=settings)
        if intent.task_type is None:
            return await record_no_task_ack(
                session=session,
                repository=repository,
                message=message,
                reason=str(intent.outcome),
                intent_outcome=str(intent.outcome),
            )
        task_type = intent.task_type
        intent_outcome = intent.outcome

    conversation = await ConversationService(session).resolve_external(
        user_id=user_id,
        channel=PLATFORM,
        external_key=(
            f"{message.adapter}:{message.conversation_type}:{message.conversation_id}"
        ),
        title=f"LangBot · {message.adapter} · {message.conversation_id}",
    )

    return await create_task_ack(
        session=session,
        repository=repository,
        settings=settings,
        user_id=user_id,
        task_type=task_type,
        conversation_id=conversation.id,
        message=message,
        intent_outcome=intent_outcome,
        task_handoff=task_handoff,
    )


def normalize_message(payload: LangBotWebhookRequest) -> NormalizedLangBotMessage:
    """规范化 message。

    Args:
        payload: payload 参数。
    """
    return NormalizedLangBotMessage(
        platform=PLATFORM,
        adapter=payload.adapter,
        sender_id=payload.sender.id,
        conversation_id=payload.conversation.id,
        conversation_type=payload.conversation.type,
        text=payload.message.text,
        message_id=payload.message_id,
    )


def platform_sender_key(*, adapter: str, sender_id: str) -> str:
    """处理 platform sender key。

    Args:
        adapter: adapter 参数。
        sender_id: sender_id 参数。
    """
    return f"{adapter}:{sender_id}"


async def record_no_task_ack(
    *,
    session: AsyncSession,
    repository: MessageRepository,
    message: NormalizedLangBotMessage,
    reason: str,
    intent_outcome: str | None,
) -> dict[str, object]:
    """记录 no task ack。

    Args:
        session: session 参数。
        repository: repository 参数。
        message: message 参数。
        reason: reason 参数。
        intent_outcome: intent_outcome 参数。
    """
    try:
        await repository.create_processed_message(
            ProcessedMessageCreate(
                platform=PLATFORM,
                message_id=message.message_id,
                adapter=message.adapter,
                sender_id=message.sender_id,
                conversation_type=message.conversation_type,
                message_text=message.text.strip(),
                intent_outcome=intent_outcome,
                chat_id=message.conversation_id,
                response_target=_response_target(message),
                reason=reason,
            )
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return ack(REASON_DUPLICATE)

    return ack(reason, message=message)


async def create_task_ack(
    *,
    session: AsyncSession,
    repository: MessageRepository,
    settings: Settings,
    user_id: str,
    task_type: str,
    conversation_id: str,
    message: NormalizedLangBotMessage,
    intent_outcome: str | None,
    task_handoff: Callable[[str], bool] | None = None,
) -> dict[str, object]:
    """创建 task ack。

    Args:
        session: session 参数。
        repository: repository 参数。
        settings: settings 参数。
        user_id: user_id 参数。
        task_type: task_type 参数。
        conversation_id: conversation_id 参数。
        message: message 参数。
        intent_outcome: intent_outcome 参数。
        task_handoff: task_handoff 参数。
    """
    try:
        processed_message = await repository.create_processed_message(
            ProcessedMessageCreate(
                platform=PLATFORM,
                message_id=message.message_id,
                adapter=message.adapter,
                sender_id=message.sender_id,
                conversation_type=message.conversation_type,
                message_text=message.text.strip(),
                intent_outcome=intent_outcome,
                chat_id=message.conversation_id,
                response_target=_response_target(message),
                reason=REASON_TASK_CREATED,
                delivery_status="pending",
            )
        )
        task = await TaskService(session).create_task(
            user_id=user_id,
            platform=PLATFORM,
            task_type=task_type,
            input_text=message.text,
            conversation_id=conversation_id,
            commit=False,
        )
        processed_message.task_id = task.id
        await session.commit()
        await session.refresh(task)
    except IntegrityError:
        await session.rollback()
        return ack(REASON_DUPLICATE, message=message)
    except TaskServiceError as exc:
        await session.rollback()
        raise AppError(
            code=exc.code,
            message=str(exc),
            status_code=exc.status_code,
        ) from exc

    try:
        if task_handoff is not None:
            task_handoff(task.id)
        else:
            enqueue_task_execution(task.id, settings=settings)
    except Exception:
        pass

    return ack(REASON_TASK_CREATED, message=message, task=task)


def ack(
    reason: str,
    *,
    message: NormalizedLangBotMessage | None = None,
    task: Task | None = None,
) -> dict[str, object]:
    """确认。

    Args:
        reason: reason 参数。
        message: message 参数。
        task: task 参数。
    """
    response: dict[str, object] = {"ok": True, "reason": reason}
    if message is not None:
        response["message"] = message.as_response()
    if task is not None:
        response["task_id"] = task.id
        response["task_type"] = task.task_type
        response["task_status"] = task.status
    return response


def _response_target(message: NormalizedLangBotMessage) -> str:
    """执行 处理 response target 的内部辅助逻辑。

    Args:
        message: message 参数。
    """
    return json.dumps(
        {
            "adapter": message.adapter,
            "conversation_id": message.conversation_id,
            "conversation_type": message.conversation_type,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def enqueue_task_execution(task_id: str, *, settings: Settings | None = None) -> bool:
    """处理 enqueue task execution。

    Args:
        task_id: task_id 参数。
        settings: settings 参数。
    """
    from workers.worker import enqueue_task_execution as enqueue

    return enqueue(task_id, runtime_settings=settings)
