from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.agent_harness import AgentModelRequest, SubAgentRequest, SubAgentResult
from packages.model_gateway import GatewayMessage
from packages.observability import Observability

from .agent_model import AgentGatewayModel
from .config import Settings


class GatewaySubAgentRunner:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        observability: Observability,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.observability = observability

    async def run(self, request: SubAgentRequest) -> SubAgentResult:
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
