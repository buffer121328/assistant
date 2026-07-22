from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import ToolLog


@dataclass(frozen=True)
class ToolLogCreate:
    """表示 处理 tool log create 的后端数据结构或服务对象。"""

    task_id: str | None
    tool_name: str
    status: str
    input_text: str | None = None
    output_text: str | None = None
    error_message: str | None = None


class ToolLogRepository:
    """表示 处理 tool log repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def create_tool_log(self, data: ToolLogCreate) -> ToolLog:
        """创建 tool log。

        Args:
            data: data 参数。
        """
        tool_log = ToolLog(
            task_id=data.task_id,
            tool_name=data.tool_name,
            status=data.status,
            input_text=data.input_text,
            output_text=data.output_text,
            error_message=data.error_message,
        )
        self.session.add(tool_log)
        await self.session.flush()
        return tool_log

    async def has_successful_tool_log(self, *, task_id: str, tool_name: str) -> bool:
        """处理 has successful tool log。

        Args:
            task_id: task_id 参数。
            tool_name: tool_name 参数。
        """
        existing = await self.session.scalar(
            select(ToolLog.id)
            .where(
                ToolLog.task_id == task_id,
                ToolLog.tool_name == tool_name,
                ToolLog.status == "succeeded",
            )
            .limit(1)
        )
        return existing is not None
