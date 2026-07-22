from __future__ import annotations

from typing import Protocol


class EmailProvider(Protocol):
    """表示 处理 email provider 的后端数据结构或服务对象。"""

    async def send(
        self,
        *,
        user_id: str,
        connection_id: str,
        recipients: tuple[str, ...],
        subject: str,
        body: str,
    ) -> str:
        """处理 send。

        Args:
            user_id: user_id 参数。
            connection_id: connection_id 参数。
            recipients: recipients 参数。
            subject: subject 参数。
            body: body 参数。
        """
        ...


class CalendarProvider(Protocol):
    """表示 处理 calendar provider 的后端数据结构或服务对象。"""

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
    ) -> str:
        """创建 event。

        Args:
            user_id: user_id 参数。
            connection_id: connection_id 参数。
            title: title 参数。
            start: start 参数。
            end: end 参数。
            description: description 参数。
            idempotency_key: idempotency_key 参数。
        """
        ...
