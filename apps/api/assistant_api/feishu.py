from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, NoReturn, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .errors import AppError
from .models import Task
from .repositories import FeishuWebhookRepository, ProcessedMessageCreate
from .services import TaskService, TaskServiceError

PLATFORM = "feishu"
FEISHU_MESSAGE_EVENT = "im.message.receive_v1"
REASON_TASK_CREATED = "task_created"
REASON_DUPLICATE = "duplicate_message"
REASON_UNKNOWN_COMMAND = "unknown_command"
REASON_UNBOUND_USER = "unbound_user"
REASON_NON_TEXT = "non_text_message"

COMMAND_TO_TASK_TYPE = {
    "/plan": "plan",
    "/learn": "learn",
    "/daily": "daily",
    "/office": "office",
    "/memory": "memory",
    "/status": "status",
}

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class FeishuEventMessage:
    platform_user_id: str
    chat_id: str
    message_id: str
    message_type: str
    content: str


@dataclass(frozen=True)
class NormalizedFeishuMessage:
    platform: str
    platform_user_id: str
    chat_id: str
    text: str
    message_id: str

    def as_response(self) -> dict[str, str]:
        return {
            "platform": self.platform,
            "platform_user_id": self.platform_user_id,
            "chat_id": self.chat_id,
            "text": self.text,
            "message_id": self.message_id,
        }


def verify_feishu_signature(
    *,
    body: bytes,
    headers: Any,
    settings: Settings,
) -> None:
    timestamp = _get_header(headers, "x-feishu-request-timestamp")
    signature = _get_header(headers, "x-feishu-signature")
    if timestamp is None or signature is None:
        raise AppError(
            code="feishu_invalid_signature",
            message="Invalid Feishu signature",
            status_code=401,
        )

    expected_digest = hmac.new(
        settings.feishu_webhook_signing_secret.encode(),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    ).digest()
    expected_signature = base64.b64encode(expected_digest).decode()
    if not hmac.compare_digest(signature, expected_signature):
        raise AppError(
            code="feishu_invalid_signature",
            message="Invalid Feishu signature",
            status_code=401,
        )


async def handle_feishu_webhook(
    *,
    body: bytes,
    headers: Any,
    session: AsyncSession,
    settings: Settings,
) -> dict[str, object]:
    verify_feishu_signature(body=body, headers=headers, settings=settings)
    payload = parse_json_payload(body)
    verify_feishu_token(payload, settings)

    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge")
        if not isinstance(challenge, str) or not challenge:
            raise_unrecognized_request()
        return {"challenge": challenge}

    event_message = parse_feishu_event_message(payload)
    repository = FeishuWebhookRepository(session)
    duplicate = await repository.get_processed_message(
        platform=PLATFORM,
        message_id=event_message.message_id,
    )
    if duplicate is not None:
        return ack(REASON_DUPLICATE)

    if event_message.message_type != "text":
        return await record_no_task_ack(
            session=session,
            repository=repository,
            message_id=event_message.message_id,
            reason=REASON_NON_TEXT,
        )

    message = normalize_text_message(event_message)
    task_type = parse_task_type(message.text)
    if task_type is None:
        return await record_no_task_ack(
            session=session,
            repository=repository,
            message_id=message.message_id,
            reason=REASON_UNKNOWN_COMMAND,
            message=message,
        )

    user_id = await repository.get_user_id_by_platform_account(
        platform=PLATFORM,
        platform_user_id=message.platform_user_id,
    )
    if user_id is None:
        return await record_no_task_ack(
            session=session,
            repository=repository,
            message_id=message.message_id,
            reason=REASON_UNBOUND_USER,
            message=message,
        )

    return await create_task_ack(
        session=session,
        repository=repository,
        user_id=user_id,
        task_type=task_type,
        message=message,
    )


def parse_json_payload(body: bytes) -> JsonObject:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise_unrecognized_request()

    if not isinstance(payload, dict):
        raise_unrecognized_request()
    return cast(JsonObject, payload)


def verify_feishu_token(payload: JsonObject, settings: Settings) -> None:
    if payload.get("token") != settings.feishu_webhook_verification_token:
        raise AppError(
            code="feishu_invalid_token",
            message="Invalid Feishu verification token",
            status_code=401,
        )


def parse_feishu_event_message(payload: JsonObject) -> FeishuEventMessage:
    header = as_json_object(payload.get("header"))
    event = as_json_object(payload.get("event"))
    if header is None or event is None:
        raise_unrecognized_request()
    if header.get("event_type") != FEISHU_MESSAGE_EVENT:
        raise_unrecognized_request()

    sender = as_json_object(event.get("sender"))
    sender_id = as_json_object(sender.get("sender_id")) if sender is not None else None
    message = as_json_object(event.get("message"))
    if sender_id is None or message is None:
        raise_unrecognized_request()

    platform_user_id = required_string(sender_id, "open_id")
    message_id = required_string(message, "message_id")
    chat_id = required_string(message, "chat_id")
    message_type = required_string(message, "message_type")
    content = message.get("content", "")
    if not isinstance(content, str):
        raise_unrecognized_request()

    return FeishuEventMessage(
        platform_user_id=platform_user_id,
        chat_id=chat_id,
        message_id=message_id,
        message_type=message_type,
        content=content,
    )


def normalize_text_message(event_message: FeishuEventMessage) -> NormalizedFeishuMessage:
    text = extract_text(event_message.content)
    return NormalizedFeishuMessage(
        platform=PLATFORM,
        platform_user_id=event_message.platform_user_id,
        chat_id=event_message.chat_id,
        text=text,
        message_id=event_message.message_id,
    )


def extract_text(content: str) -> str:
    try:
        content_payload = json.loads(content)
    except json.JSONDecodeError:
        return content

    if not isinstance(content_payload, dict):
        raise_unrecognized_request()
    text = content_payload.get("text")
    if not isinstance(text, str):
        raise_unrecognized_request()
    return text


def parse_task_type(text: str) -> str | None:
    tokens = text.split(maxsplit=1)
    if not tokens:
        return None
    return COMMAND_TO_TASK_TYPE.get(tokens[0])


async def record_no_task_ack(
    *,
    session: AsyncSession,
    repository: FeishuWebhookRepository,
    message_id: str,
    reason: str,
    message: NormalizedFeishuMessage | None = None,
) -> dict[str, object]:
    try:
        await repository.create_processed_message(
            ProcessedMessageCreate(
                platform=PLATFORM,
                message_id=message_id,
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
    repository: FeishuWebhookRepository,
    user_id: str,
    task_type: str,
    message: NormalizedFeishuMessage,
) -> dict[str, object]:
    try:
        processed_message = await repository.create_processed_message(
            ProcessedMessageCreate(
                platform=PLATFORM,
                message_id=message.message_id,
                reason=REASON_TASK_CREATED,
            )
        )
        task = await TaskService(session).create_task(
            user_id=user_id,
            platform=PLATFORM,
            task_type=task_type,
            input_text=message.text,
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

    return ack(REASON_TASK_CREATED, message=message, task=task)


def ack(
    reason: str,
    *,
    message: NormalizedFeishuMessage | None = None,
    task: Task | None = None,
) -> dict[str, object]:
    response: dict[str, object] = {"ok": True, "reason": reason}
    if message is not None:
        response["message"] = message.as_response()
    if task is not None:
        response["task_id"] = task.id
        response["task_type"] = task.task_type
        response["task_status"] = task.status
    return response


def as_json_object(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    return cast(JsonObject, value)


def required_string(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise_unrecognized_request()
    return value


def _get_header(headers: Any, name: str) -> str | None:
    value = headers.get(name)
    if value is not None:
        return str(value)
    value = headers.get(name.title())
    if value is not None:
        return str(value)
    return None


def raise_unrecognized_request() -> NoReturn:
    raise AppError(
        code="feishu_unrecognized_request",
        message="Unrecognized Feishu webhook request",
        status_code=400,
    )
