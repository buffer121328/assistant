from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent import AgentModelRequest, SubAgentRequest, SubAgentResult
from models import GatewayMessage
from observability import Observability

from models.agent_model import AgentGatewayModel
from infrastructure.config import Settings


class GatewaySubAgentRunner:
    """表示 处理 gateway sub agent runner 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        observability: Observability,
    ) -> None:
        """初始化对象实例。

        Args:
            sessionmaker: sessionmaker 参数。
            settings: settings 参数。
            observability: observability 参数。
        """
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.observability = observability

    async def run(self, request: SubAgentRequest) -> SubAgentResult:
        """运行。

        Args:
            request: request 参数。
        """
        messages = (
            GatewayMessage(
                role="system",
                content=(
                    "你是受限子 Agent，只完成给定认知任务并返回最终文本。"
                    "不得调用工具、申请审批、修改计划、写记忆或扩大权限。"
                    '只输出 {"action":"final","answer":"...","plan":[]}。'
                    f"角色：{request.role[:64]}；目标：{request.objective[:1000]}；"
                    f"上下文：{request.context[:20_000]}"
                ),
            ),
        )
        async with self.sessionmaker() as session:
            model = AgentGatewayModel(
                session=session,
                settings=self.settings,
                observability=self.observability,
            )
            decision = await model.decide(
                AgentModelRequest(
                    task_id=request.task_id,
                    user_id=request.user_id,
                    task_type="agent",
                    messages=messages,
                )
            )
            if decision.action != "final" or not decision.answer:
                await session.rollback()
                raise RuntimeError("Subagent attempted a non-final action")
            await session.commit()
        return SubAgentResult(
            step_index=request.step_index,
            role=request.role,
            content=decision.answer,
        )
