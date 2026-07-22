from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import ModelLog


@dataclass(frozen=True)
class ModelLogCreate:
    """表示 处理 model log create 的后端数据结构或服务对象。"""

    task_id: str | None
    model_class: str | None
    request_text: str | None
    response_text: str | None
    error_message: str | None
    agent_run_id: str | None = None


class ModelLogRepository:
    """表示 处理 model log repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def create_model_log(self, data: ModelLogCreate) -> ModelLog:
        """创建 model log。

        Args:
            data: data 参数。
        """
        model_log = ModelLog(
            task_id=data.task_id,
            agent_run_id=data.agent_run_id,
            model_class=data.model_class,
            request_text=data.request_text,
            response_text=data.response_text,
            error_message=data.error_message,
        )
        self.session.add(model_log)
        await self.session.flush()
        return model_log
