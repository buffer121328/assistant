from __future__ import annotations

from typing import Protocol


class EmailProvider(Protocol):
    async def send(
        self,
        *,
        user_id: str,
        connection_id: str,
        recipients: tuple[str, ...],
        subject: str,
        body: str,
    ) -> str: ...


class CalendarProvider(Protocol):
    async def create_event(
        self,
        *,
        user_id: str,
        connection_id: str,
        title: str,
        start: str,
        end: str,
        description: str,
        idempotency_key: str,
    ) -> str: ...
