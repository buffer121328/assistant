from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import PlatformAccount, ProcessedMessage, utc_now


@dataclass(frozen=True)
class ProcessedMessageCreate:
    """表示 处理 processed message create 的后端数据结构或服务对象。"""

    platform: str
    message_id: str
    reason: str
    adapter: str | None = None
    sender_id: str | None = None
    conversation_type: str | None = None
    message_text: str | None = None
    intent_outcome: str | None = None
    chat_id: str | None = None
    response_target: str | None = None
    task_id: str | None = None
    delivery_status: str | None = None
    delivery_error_summary: str | None = None
    delivery_result_json: str | None = None


class MessageRepository:
    """表示 处理 message repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def get_user_id_by_platform_account(
        self,
        *,
        platform: str,
        platform_user_id: str,
    ) -> str | None:
        """获取 user id by platform account。

        Args:
            platform: platform 参数。
            platform_user_id: platform_user_id 参数。
        """
        return await self.session.scalar(
            select(PlatformAccount.user_id).where(
                PlatformAccount.platform == platform,
                PlatformAccount.platform_user_id == platform_user_id,
            )
        )

    async def get_processed_message(
        self,
        *,
        platform: str,
        adapter: str | None,
        message_id: str,
    ) -> ProcessedMessage | None:
        """获取 processed message。

        Args:
            platform: platform 参数。
            adapter: adapter 参数。
            message_id: message_id 参数。
        """
        return await self.session.scalar(
            select(ProcessedMessage).where(
                ProcessedMessage.platform == platform,
                ProcessedMessage.adapter == adapter,
                ProcessedMessage.message_id == message_id,
            )
        )

    async def create_processed_message(
        self,
        data: ProcessedMessageCreate,
    ) -> ProcessedMessage:
        """创建 processed message。

        Args:
            data: data 参数。
        """
        processed_message = ProcessedMessage(
            platform=data.platform,
            message_id=data.message_id,
            adapter=data.adapter,
            sender_id=data.sender_id,
            conversation_type=data.conversation_type,
            message_text=data.message_text,
            intent_outcome=data.intent_outcome,
            chat_id=data.chat_id,
            response_target=data.response_target,
            reason=data.reason,
            task_id=data.task_id,
            delivery_status=data.delivery_status,
            delivery_error_summary=data.delivery_error_summary,
            delivery_result_json=data.delivery_result_json,
        )
        self.session.add(processed_message)
        await self.session.flush()
        return processed_message

    async def get_task_dispatch_record(self, task_id: str) -> ProcessedMessage | None:
        """获取 task dispatch record。

        Args:
            task_id: task_id 参数。
        """
        return await self.session.scalar(
            select(ProcessedMessage)
            .where(
                ProcessedMessage.reason == "task_created",
                ProcessedMessage.task_id == task_id,
            )
            .order_by(ProcessedMessage.created_at.asc(), ProcessedMessage.id.asc())
            .limit(1)
        )

    async def list_recent_bridge_sessions(
        self,
        *,
        limit: int = 20,
    ) -> list[ProcessedMessage]:
        """列出 recent bridge sessions。

        Args:
            limit: limit 参数。
        """
        result = await self.session.scalars(
            select(ProcessedMessage)
            .where(ProcessedMessage.platform == "langbot")
            .order_by(ProcessedMessage.created_at.desc(), ProcessedMessage.id.desc())
            .limit(limit)
        )
        return list(result)

    async def get_bridge_session(self, message_id: str) -> ProcessedMessage | None:
        """获取 bridge session。

        Args:
            message_id: message_id 参数。
        """
        return await self.session.scalar(
            select(ProcessedMessage).where(
                ProcessedMessage.platform == "langbot",
                ProcessedMessage.message_id == message_id,
            )
        )

    async def record_delivery_attempt(
        self,
        *,
        task_id: str,
        status: str,
        error_summary: str | None = None,
        result_json: str | None = None,
        delivery_status: str | None = None,
    ) -> ProcessedMessage | None:
        """记录 delivery attempt。

        Args:
            task_id: task_id 参数。
            status: status 参数。
            error_summary: error_summary 参数。
            result_json: result_json 参数。
            delivery_status: delivery_status 参数。
        """
        record = await self.get_task_dispatch_record(task_id)
        if record is None:
            return None

        record.delivery_attempt_count += 1
        record.delivery_status = delivery_status or status
        record.delivery_error_summary = error_summary
        record.delivery_result_json = result_json
        record.delivery_last_attempt_at = utc_now()
        return record
