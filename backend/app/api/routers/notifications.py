from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from integrations.notifications import NotificationError, ReminderService

from infrastructure.persistence.database import get_session
from app.support.errors import AppError
from domain.models import NotificationOutbox
from app.api.schemas import (
    DesktopNotificationListResponse,
    DesktopNotificationResponse,
    ReminderActorRequest,
    ReminderCreateRequest,
    ReminderListResponse,
    ReminderResponse,
)

router = APIRouter()


def raise_notification_error(exc: NotificationError) -> None:
    """处理 raise notification error。

    Args:
        exc: exc 参数。
    """
    raise AppError(
        code=exc.code,
        message="Notification operation failed.",
        status_code=404 if exc.code.endswith("not_found") else 409,
    ) from exc


def reminder_response(item: object) -> ReminderResponse:
    """处理 reminder response。

    Args:
        item: item 参数。
    """
    return ReminderResponse(
        reminder_id=str(getattr(item, "id")),
        user_id=str(getattr(item, "user_id")),
        title=str(getattr(item, "title")),
        message=str(getattr(item, "message")),
        due_at=getattr(item, "due_at"),
        channel=str(getattr(item, "channel")),
        status=str(getattr(item, "status")),
        cancelled_at=getattr(item, "cancelled_at"),
    )


@router.post(
    "/api/reminders",
    response_model=ReminderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_reminder(
    payload: ReminderCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReminderResponse:
    """创建 reminder。

    Args:
        payload: payload 参数。
        session: session 参数。
    """
    try:
        reminder = await ReminderService(session).create(
            user_id=payload.user_id,
            title=payload.title,
            message=payload.message,
            due_at=payload.due_at,
            channel=payload.channel,
        )
    except NotificationError as exc:
        raise_notification_error(exc)
    return reminder_response(reminder)


@router.get("/api/reminders", response_model=ReminderListResponse)
async def list_reminders(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReminderListResponse:
    """列出 reminders。

    Args:
        user_id: user_id 参数。
        session: session 参数。
    """
    reminders = await ReminderService(session).list(user_id=user_id)
    items: list[ReminderResponse] = []
    for reminder in reminders:
        outcome = await session.scalar(
            select(NotificationOutbox)
            .where(NotificationOutbox.reminder_id == reminder.id)
            .order_by(
                NotificationOutbox.updated_at.desc(), NotificationOutbox.id.desc()
            )
            .limit(1)
        )
        response = reminder_response(reminder)
        if outcome is not None:
            response.delivery_status = outcome.status
            response.last_error_code = outcome.last_error_code
        items.append(response)
    return ReminderListResponse(items=items)


@router.post("/api/reminders/{reminder_id}/cancel", response_model=ReminderResponse)
async def cancel_reminder(
    reminder_id: str,
    payload: ReminderActorRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReminderResponse:
    """处理 cancel reminder。

    Args:
        reminder_id: reminder_id 参数。
        payload: payload 参数。
        session: session 参数。
    """
    try:
        reminder = await ReminderService(session).cancel(
            user_id=payload.user_id, reminder_id=reminder_id
        )
    except NotificationError as exc:
        raise_notification_error(exc)
    return reminder_response(reminder)


@router.get("/api/notifications/poll", response_model=DesktopNotificationListResponse)
async def poll_notifications(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DesktopNotificationListResponse:
    """处理 poll notifications。

    Args:
        user_id: user_id 参数。
        session: session 参数。
    """
    items = await ReminderService(session).poll_desktop(user_id=user_id)
    return DesktopNotificationListResponse(
        items=[DesktopNotificationResponse(**item.__dict__) for item in items]
    )


@router.post(
    "/api/notifications/{outbox_id}/ack", status_code=status.HTTP_204_NO_CONTENT
)
async def acknowledge_notification(
    outbox_id: str,
    payload: ReminderActorRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """处理 acknowledge notification。

    Args:
        outbox_id: outbox_id 参数。
        payload: payload 参数。
        session: session 参数。
    """
    try:
        await ReminderService(session).acknowledge_desktop(
            user_id=payload.user_id, outbox_id=outbox_id
        )
    except NotificationError as exc:
        raise_notification_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
