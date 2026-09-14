"""Conversation 级模型 token 预算：预留、结算与安全摘要。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import Conversation
from runtime.budget import BudgetExceededError

CONVERSATION_TOKEN_LIMIT = 500_000
CONVERSATION_TOKEN_LIMIT_STOP_REASON = "conversation_token_limit_exceeded"


@dataclass(frozen=True)
class ConversationTokenReservation:
    """一次 provider 调用在 Conversation 账本中占用的有界额度。"""

    conversation_id: str  # 所属会话 ID。
    input_tokens: int  # 本次调用预留的输入 Token 数。
    output_tokens: int  # 本次调用预留的输出 Token 数。


@dataclass(frozen=True)
class ConversationTokenSnapshot:
    """Conversation 级真实 token 用量快照。"""

    conversation_id: str  # 所属会话 ID。
    token_limit: int  # 会话累计 Token 上限。
    used_input_tokens: int  # 已结算的输入 Token 数。
    used_output_tokens: int  # 已结算的输出 Token 数。
    reserved_input_tokens: int  # 尚未结算的输入 Token 数。
    reserved_output_tokens: int  # 尚未结算的输出 Token 数。
    status: str  # 当前预算状态。
    blocked_reason: str | None  # 预算被阻断时的原因。

    @property
    def used_total_tokens(self) -> int:
        """返回已结算的输入与输出 Token 总数。"""
        return self.used_input_tokens + self.used_output_tokens

    @property
    def reserved_total_tokens(self) -> int:
        """返回尚未结算的预留 Token 总数。"""
        return self.reserved_input_tokens + self.reserved_output_tokens

    @property
    def remaining_tokens(self) -> int:
        """返回扣除已用和预留额度后的可用 Token 数。"""
        return max(0, self.token_limit - self.used_total_tokens - self.reserved_total_tokens)

    @property
    def settled_remaining_tokens(self) -> int:
        """返回仅扣除已结算用量后的剩余 Token 数。"""
        return max(0, self.token_limit - self.used_total_tokens)

    def summary(self) -> dict[str, Any]:
        """返回可写入日志和 API 的有界摘要。"""
        return {
            "conversation_id": self.conversation_id,
            "token_limit": self.token_limit,
            "used_input_tokens": self.used_input_tokens,
            "used_output_tokens": self.used_output_tokens,
            "used_total_tokens": self.used_total_tokens,
            "reserved_input_tokens": self.reserved_input_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "reserved_total_tokens": self.reserved_total_tokens,
            "remaining_tokens": self.settled_remaining_tokens,
            "available_tokens": self.remaining_tokens,
            "status": self.status,
            "blocked_reason": self.blocked_reason,
        }


class ConversationTokenBudget:
    """基于 Conversation 行锁的真实模型 token 累计账本。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定预算账本使用的数据库会话。

        Args:
            session: 当前数据库异步会话。
        """
        self.session = session

    async def reserve(
        self,
        *,
        conversation_id: str,
        user_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> ConversationTokenReservation:
        """为下一次模型调用预留预算，不足时在 provider 调用前拒绝。

        Args:
            conversation_id: 目标会话 ID。
            user_id: 发起调用的用户 ID。
            input_tokens: 预估的输入 Token 数。
            output_tokens: 期望预留的输出 Token 上限。
        """
        conversation = await self._locked_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        snapshot = _snapshot(conversation)
        requested_input = max(0, input_tokens)
        requested_output = max(1, output_tokens)
        available = snapshot.remaining_tokens
        if snapshot.status == "exhausted" or available <= 0:
            self._raise_limit(snapshot)
        if requested_input >= available:
            self._raise_limit(snapshot)

        # 输入估算来自当前请求；输出上限可按 Conversation 剩余空间安全收窄，不能扩张。
        granted_output = min(requested_output, available - requested_input)
        if granted_output < 1:
            self._raise_limit(snapshot)
        conversation.reserved_input_tokens += requested_input
        conversation.reserved_output_tokens += granted_output
        await self.session.flush()
        return ConversationTokenReservation(
            conversation_id=conversation_id,
            input_tokens=requested_input,
            output_tokens=granted_output,
        )

    async def settle(
        self,
        reservation: ConversationTokenReservation,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> ConversationTokenSnapshot:
        """将 provider 实际 usage 结算到账本，并释放未使用的预留额度。

        Args:
            reservation: 本次调用此前获得的预算预留。
            input_tokens: 实际消耗的输入 Token 数。
            output_tokens: 实际消耗的输出 Token 数。
        """
        conversation = await self._locked_conversation(
            conversation_id=reservation.conversation_id,
            user_id=None,
        )
        conversation.reserved_input_tokens = max(
            0, conversation.reserved_input_tokens - reservation.input_tokens
        )
        conversation.reserved_output_tokens = max(
            0, conversation.reserved_output_tokens - reservation.output_tokens
        )
        conversation.used_input_tokens += max(0, input_tokens)
        conversation.used_output_tokens += max(0, output_tokens)
        if (
            conversation.used_input_tokens + conversation.used_output_tokens
            >= conversation.token_limit
        ):
            conversation.token_status = "exhausted"
            conversation.token_blocked_reason = CONVERSATION_TOKEN_LIMIT_STOP_REASON
        await self.session.flush()
        return _snapshot(conversation)

    async def release(
        self,
        reservation: ConversationTokenReservation,
    ) -> ConversationTokenSnapshot:
        """provider 失败时释放预留额度，不消费会话累计用量。

        Args:
            reservation: 需要释放的预算预留。
        """
        conversation = await self._locked_conversation(
            conversation_id=reservation.conversation_id,
            user_id=None,
        )
        conversation.reserved_input_tokens = max(
            0, conversation.reserved_input_tokens - reservation.input_tokens
        )
        conversation.reserved_output_tokens = max(
            0, conversation.reserved_output_tokens - reservation.output_tokens
        )
        await self.session.flush()
        return _snapshot(conversation)

    async def snapshot(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ConversationTokenSnapshot:
        """读取 owner-scoped 的会话预算快照。

        Args:
            conversation_id: 目标会话 ID。
            user_id: 用于 owner 隔离的用户 ID。
        """
        conversation = await self._locked_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            lock=False,
        )
        return _snapshot(conversation)

    async def _locked_conversation(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
        lock: bool = True,
    ) -> Conversation:
        """按属主范围读取会话，并按需加行锁。

        Args:
            conversation_id: 目标会话 ID。
            user_id: 用于 owner 隔离的用户 ID，可为 None。
            lock: 是否对目标会话加数据库行锁。
        """
        conditions = [Conversation.id == conversation_id]
        if user_id is not None:
            conditions.append(Conversation.user_id == user_id)
        statement = select(Conversation).where(*conditions)
        if lock:
            statement = statement.with_for_update()
        conversation = await self.session.scalar(statement)
        if conversation is None:
            raise ValueError("Conversation budget target was not found")
        return conversation

    @staticmethod
    def _raise_limit(snapshot: ConversationTokenSnapshot) -> None:
        """以统一停止原因抛出预算超限错误。

        Args:
            snapshot: 当前会话的预算快照。
        """
        raise BudgetExceededError(
            CONVERSATION_TOKEN_LIMIT_STOP_REASON,
            snapshot.summary(),
        )


def _snapshot(conversation: Conversation) -> ConversationTokenSnapshot:
    """从持久化会话对象构造归一化的预算快照。

    Args:
        conversation: 需要读取预算字段的会话对象。
    """
    return ConversationTokenSnapshot(
        conversation_id=conversation.id,
        token_limit=max(1, conversation.token_limit or CONVERSATION_TOKEN_LIMIT),
        used_input_tokens=max(0, conversation.used_input_tokens),
        used_output_tokens=max(0, conversation.used_output_tokens),
        reserved_input_tokens=max(0, conversation.reserved_input_tokens),
        reserved_output_tokens=max(0, conversation.reserved_output_tokens),
        status=conversation.token_status or "active",
        blocked_reason=conversation.token_blocked_reason,
    )
