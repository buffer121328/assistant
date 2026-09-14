from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from application.session_context.conversations import ConversationService
from domain.models import Base, User
from agent import AgentModelRequest
from infrastructure.settings.config import Settings
from model_gateway import GatewayMessage, GatewayResult, GatewayUsage
from model_gateway.agent_model import AgentGatewayModel, AgentModelGatewayError
from runtime.budget import BudgetExceededError
from runtime.conversation_budget import (
    CONVERSATION_TOKEN_LIMIT_STOP_REASON,
    ConversationTokenBudget,
)


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/conversation-token-budget.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _conversation(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    async with sessionmaker() as session:
        user = User(display_name="Conversation budget owner")
        session.add(user)
        await session.flush()
        conversation = await ConversationService(session).create(
            user_id=user.id,
            title="预算会话",
            commit=False,
        )
        await session.commit()
        return user.id, conversation.id


@pytest.mark.asyncio
async def test_conversation_budget_accumulates_usage_across_task_runs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id, conversation_id = await _conversation(sessionmaker)

    async with sessionmaker() as session:
        budget = ConversationTokenBudget(session)
        first = await budget.reserve(
            conversation_id=conversation_id,
            user_id=user_id,
            input_tokens=80_000,
            output_tokens=40_000,
        )
        first_snapshot = await budget.settle(
            first,
            input_tokens=80_000,
            output_tokens=40_000,
        )
        await session.commit()

    assert first_snapshot.used_total_tokens == 120_000
    assert first_snapshot.settled_remaining_tokens == 80_000

    async with sessionmaker() as session:
        budget = ConversationTokenBudget(session)
        second = await budget.reserve(
            conversation_id=conversation_id,
            user_id=user_id,
            input_tokens=50_000,
            output_tokens=30_000,
        )
        second_snapshot = await budget.settle(
            second,
            input_tokens=50_000,
            output_tokens=30_000,
        )
        await session.commit()

    assert second_snapshot.used_input_tokens == 130_000
    assert second_snapshot.used_output_tokens == 70_000
    assert second_snapshot.used_total_tokens == 200_000
    assert second_snapshot.status == "exhausted"
    assert second_snapshot.blocked_reason == CONVERSATION_TOKEN_LIMIT_STOP_REASON

    async with sessionmaker() as session:
        with pytest.raises(BudgetExceededError) as exc_info:
            await ConversationTokenBudget(session).reserve(
                conversation_id=conversation_id,
                user_id=user_id,
                input_tokens=1,
                output_tokens=1,
            )

    assert exc_info.value.stop_reason == CONVERSATION_TOKEN_LIMIT_STOP_REASON


@pytest.mark.asyncio
async def test_conversation_budget_releases_failed_call_reservations_and_stats_are_authoritative(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id, conversation_id = await _conversation(sessionmaker)

    async with sessionmaker() as session:
        budget = ConversationTokenBudget(session)
        reservation = await budget.reserve(
            conversation_id=conversation_id,
            user_id=user_id,
            input_tokens=1_000,
            output_tokens=10_000,
        )
        released = await budget.release(reservation)
        settled = await budget.settle(
            await budget.reserve(
                conversation_id=conversation_id,
                user_id=user_id,
                input_tokens=1_000,
                output_tokens=10_000,
            ),
            input_tokens=900,
            output_tokens=600,
        )
        stats = await ConversationService(session).token_stats(
            conversation_id=conversation_id,
            user_id=user_id,
        )

    assert released.reserved_total_tokens == 0
    assert settled.used_total_tokens == 1_500
    assert stats.used_input_tokens == 900
    assert stats.used_output_tokens == 600
    assert stats.used_total_tokens == 1_500
    assert stats.remaining_tokens == 198_500
    assert stats.total_estimated_tokens == 0
    assert stats.token_limit == 200_000


class _ResultAdapter:
    async def chat(self, request, model_class: str) -> GatewayResult:
        return GatewayResult(
            provider="test",
            model=f"{model_class}-test",
            content='{"action":"final","answer":"已完成","plan":[]}',
            usage=GatewayUsage(input_tokens=321, output_tokens=123),
            latency_ms=1,
        )


class _FailingAdapter:
    async def chat(self, request, model_class: str) -> GatewayResult:
        raise RuntimeError("provider failed")


@pytest.mark.asyncio
async def test_agent_gateway_settles_actual_usage_and_releases_failed_reservations(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Provider usage updates one Conversation; failed calls do not leave a hold."""
    user_id, conversation_id = await _conversation(sessionmaker)
    request = AgentModelRequest(
        task_id="conversation-budget-task",
        user_id=user_id,
        task_type="agent",
        messages=(GatewayMessage(role="user", content="请确认预算记账"),),
        conversation_id=conversation_id,
    )

    async with sessionmaker() as session:
        model = AgentGatewayModel(
            session=session,
            settings=Settings(),
            adapter=_ResultAdapter(),
        )
        decision = await model.decide(request)
        stats = await ConversationService(session).token_stats(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        await session.commit()

    assert decision.answer == "已完成"
    assert stats.used_input_tokens == 321
    assert stats.used_output_tokens == 123
    assert stats.used_total_tokens == 444
    assert stats.reserved_total_tokens == 0

    async with sessionmaker() as session:
        model = AgentGatewayModel(
            session=session,
            settings=Settings(),
            adapter=_FailingAdapter(),
        )
        with pytest.raises(AgentModelGatewayError):
            await model.decide(request)
        stats_after_failure = await ConversationService(session).token_stats(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        await session.commit()

    assert stats_after_failure.used_total_tokens == 444
    assert stats_after_failure.reserved_total_tokens == 0
