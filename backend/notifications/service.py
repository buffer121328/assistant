from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import (
    DeliveryAttempt,
    NotificationOutbox,
    ProcessedMessage,
    Reminder,
    Task,
    User,
)


MAX_DELIVERY_ATTEMPTS = 3
DELIVERY_LEASE = timedelta(minutes=5)


class NotificationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LangBotNotificationClient(Protocol):
    async def send_message(
        self,
        *,
        adapter: str,
        conversation_id: str,
        conversation_type: str,
        text: str,
        idempotency_key: str,
    ) -> Any: ...


@dataclass(frozen=True)
class DesktopNotification:
    outbox_id: str
    reminder_id: str
    title: str
    message: str
    due_at: datetime


class ReminderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: str,
        title: str,
        message: str,
        due_at: datetime,
        channel: str,
    ) -> Reminder:
        if await self.session.get(User, user_id) is None:
            raise NotificationError("reminder_user_not_found")
        if channel not in {"desktop", "langbot"}:
            raise NotificationError("reminder_channel_invalid")
        if due_at.tzinfo is None:
            raise NotificationError("reminder_due_at_timezone_required")
        reminder = Reminder(
            user_id=user_id,
            title=title.strip()[:255],
            message=message.strip()[:10_000],
            due_at=due_at.astimezone(UTC),
            channel=channel,
            status="pending",
        )
        if not reminder.title or not reminder.message:
            raise NotificationError("reminder_content_invalid")
        self.session.add(reminder)
        await self.session.commit()
        await self.session.refresh(reminder)
        return reminder

    async def list(self, *, user_id: str) -> tuple[Reminder, ...]:
        reminders = await self.session.scalars(
            select(Reminder)
            .where(Reminder.user_id == user_id)
            .order_by(Reminder.due_at.desc(), Reminder.id)
        )
        return tuple(reminders)

    async def cancel(self, *, user_id: str, reminder_id: str) -> Reminder:
        reminder = await self.session.scalar(
            select(Reminder).where(
                Reminder.id == reminder_id, Reminder.user_id == user_id
            )
        )
        if reminder is None:
            raise NotificationError("reminder_not_found")
        if reminder.status == "completed":
            raise NotificationError("reminder_already_delivered")
        if reminder.status != "cancelled":
            reminder.status = "cancelled"
            reminder.cancelled_at = datetime.now(UTC)
            await self.session.execute(
                update(NotificationOutbox)
                .where(
                    NotificationOutbox.reminder_id == reminder.id,
                    NotificationOutbox.status.in_(("pending", "retry")),
                )
                .values(status="cancelled")
            )
            await self.session.commit()
        return reminder

    async def materialize_due(self, *, now: datetime | None = None) -> tuple[str, ...]:
        evaluated_at = now or datetime.now(UTC)
        reminders = await self.session.scalars(
            select(Reminder)
            .where(Reminder.status == "pending", Reminder.due_at <= evaluated_at)
            .order_by(Reminder.due_at, Reminder.id)
        )
        created: list[str] = []
        for reminder in reminders:
            key = f"reminder:{reminder.id}:{reminder.due_at.isoformat()}"
            existing = await self.session.scalar(
                select(NotificationOutbox.id).where(
                    NotificationOutbox.idempotency_key == key
                )
            )
            if existing is not None:
                continue
            outbox = NotificationOutbox(
                reminder_id=reminder.id,
                user_id=reminder.user_id,
                channel=reminder.channel,
                idempotency_key=key,
                status="pending",
                available_at=evaluated_at,
                attempt_count=0,
            )
            self.session.add(outbox)
            await self.session.flush()
            created.append(outbox.id)
        await self.session.commit()
        return tuple(created)

    async def poll_desktop(
        self, *, user_id: str, now: datetime | None = None
    ) -> tuple[DesktopNotification, ...]:
        evaluated_at = now or datetime.now(UTC)
        rows = await self.session.execute(
            select(NotificationOutbox, Reminder)
            .join(Reminder, Reminder.id == NotificationOutbox.reminder_id)
            .where(
                NotificationOutbox.user_id == user_id,
                NotificationOutbox.channel == "desktop",
                NotificationOutbox.status.in_(("pending", "retry")),
                NotificationOutbox.available_at <= evaluated_at,
                Reminder.status != "cancelled",
            )
            .order_by(NotificationOutbox.available_at, NotificationOutbox.id)
        )
        return tuple(
            DesktopNotification(
                outbox_id=outbox.id,
                reminder_id=reminder.id,
                title=reminder.title,
                message=reminder.message,
                due_at=reminder.due_at,
            )
            for outbox, reminder in rows
        )

    async def acknowledge_desktop(self, *, user_id: str, outbox_id: str) -> None:
        row = await self.session.execute(
            select(NotificationOutbox, Reminder)
            .join(Reminder, Reminder.id == NotificationOutbox.reminder_id)
            .where(
                NotificationOutbox.id == outbox_id,
                NotificationOutbox.user_id == user_id,
                NotificationOutbox.channel == "desktop",
            )
        )
        item = row.one_or_none()
        if item is None:
            raise NotificationError("notification_not_found")
        outbox, reminder = item
        if outbox.status != "delivered":
            now = datetime.now(UTC)
            outbox.status = "delivered"
            outbox.delivered_at = now
            reminder.status = "completed"
            self.session.add(
                DeliveryAttempt(outbox_id=outbox.id, status="delivered")
            )
            await self.session.commit()


async def deliver_langbot_due(
    *,
    session: AsyncSession,
    client: LangBotNotificationClient,
    now: datetime | None = None,
) -> tuple[str, ...]:
    evaluated_at = now or datetime.now(UTC)
    await session.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.status == "sending",
            NotificationOutbox.available_at <= evaluated_at,
        )
        .values(
            status="retry",
            available_at=evaluated_at,
            last_error_code="delivery_interrupted",
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    candidate_ids = tuple(
        await session.scalars(
            select(NotificationOutbox.id)
            .join(Reminder, Reminder.id == NotificationOutbox.reminder_id)
            .where(
                NotificationOutbox.channel == "langbot",
                NotificationOutbox.status.in_(("pending", "retry")),
                NotificationOutbox.available_at <= evaluated_at,
                Reminder.status != "cancelled",
            )
            .order_by(NotificationOutbox.available_at, NotificationOutbox.id)
        )
    )
    delivered: list[str] = []
    for outbox_id in candidate_ids:
        claimed_id = await session.scalar(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.id == outbox_id,
                NotificationOutbox.status.in_(("pending", "retry")),
                NotificationOutbox.available_at <= evaluated_at,
            )
            .values(
                status="sending",
                available_at=evaluated_at + DELIVERY_LEASE,
                attempt_count=NotificationOutbox.attempt_count + 1,
                last_error_code=None,
            )
            .returning(NotificationOutbox.id)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        if claimed_id is None:
            continue
        row = await session.execute(
            select(NotificationOutbox, Reminder)
            .join(Reminder, Reminder.id == NotificationOutbox.reminder_id)
            .where(
                NotificationOutbox.id == claimed_id,
                Reminder.status != "cancelled",
            )
            .execution_options(populate_existing=True)
        )
        item = row.one_or_none()
        if item is None:
            await session.execute(
                update(NotificationOutbox)
                .where(
                    NotificationOutbox.id == claimed_id,
                    NotificationOutbox.status == "sending",
                )
                .values(status="cancelled")
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            continue
        outbox, reminder = item
        target = await _langbot_target(session, reminder.user_id)
        if target is None:
            _failed_attempt(outbox, session, evaluated_at, "langbot_target_missing")
            await session.commit()
            continue
        try:
            await client.send_message(
                adapter=target["adapter"],
                conversation_id=target["conversation_id"],
                conversation_type=target["conversation_type"],
                text=f"{reminder.title}\n{reminder.message}"[:10_000],
                idempotency_key=outbox.idempotency_key,
            )
        except Exception:
            _failed_attempt(outbox, session, evaluated_at, "langbot_delivery_failed")
            await session.commit()
            continue
        outbox.status = "delivered"
        outbox.delivered_at = evaluated_at
        outbox.last_error_code = None
        reminder.status = "completed"
        session.add(DeliveryAttempt(outbox_id=outbox.id, status="delivered"))
        await session.commit()
        delivered.append(outbox.id)
    return tuple(delivered)


def _failed_attempt(
    outbox: NotificationOutbox,
    session: AsyncSession,
    now: datetime,
    error_code: str,
) -> None:
    outbox.last_error_code = error_code
    outbox.status = "dead" if outbox.attempt_count >= MAX_DELIVERY_ATTEMPTS else "retry"
    if outbox.status == "retry":
        outbox.available_at = now + timedelta(minutes=outbox.attempt_count)
    session.add(
        DeliveryAttempt(outbox_id=outbox.id, status=outbox.status, error_code=error_code)
    )


async def _langbot_target(
    session: AsyncSession, user_id: str
) -> dict[str, str] | None:
    record = await session.scalar(
        select(ProcessedMessage)
        .join(Task, Task.id == ProcessedMessage.task_id)
        .where(
            Task.user_id == user_id,
            Task.platform == "langbot",
            ProcessedMessage.reason == "task_created",
            ProcessedMessage.response_target.is_not(None),
        )
        .order_by(ProcessedMessage.created_at.desc(), ProcessedMessage.id.desc())
        .limit(1)
    )
    if record is None or not record.response_target:
        return None
    try:
        value = json.loads(record.response_target)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    keys = ("adapter", "conversation_id", "conversation_type")
    if any(not isinstance(value.get(key), str) or not value[key] for key in keys):
        return None
    return {key: value[key] for key in keys}
