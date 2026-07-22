from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from application.conversation_memory import (
    ConversationMemoryService,
    SummaryDraft,
)
from application.conversations import ConversationError, ConversationService
from domain.models import Base, ConversationMessage, ConversationSummary, User


class SuccessfulSummarizer:
    async def summarize(
        self,
        *,
        messages: Sequence[ConversationMessage],
        previous: ConversationSummary | None,
    ) -> SummaryDraft:
        del messages, previous
        return SummaryDraft(
            current_goal="完成 V6-02",
            confirmed_facts=("使用 PostgreSQL",),
            decisions=("废弃固定 12 条",),
            discarded_information=("旧的浅色偏好",),
        )


class FailingSummarizer:
    async def summarize(
        self,
        *,
        messages: Sequence[ConversationMessage],
        previous: ConversationSummary | None,
    ) -> SummaryDraft:
        del messages, previous
        raise RuntimeError("synthetic summarizer failure")


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/short-term.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_conversation(
    sessionmaker: async_sessionmaker[AsyncSession], name: str = "owner"
) -> tuple[User, str]:
    async with sessionmaker() as session:
        user = User(display_name=name)
        session.add(user)
        await session.flush()
        conversation = await ConversationService(session).create(
            user_id=user.id, commit=False
        )
        for index in range(1, 15):
            await ConversationService(session).append_message(
                conversation_id=conversation.id,
                user_id=user.id,
                role="user" if index % 2 else "assistant",
                content=f"消息 {index}",
            )
        await session.commit()
        return user, conversation.id


@pytest.mark.asyncio
async def test_summary_records_source_range_version_and_preserves_discarded_info(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, conversation_id = await create_conversation(sessionmaker)
    async with sessionmaker() as session:
        summary = await ConversationMemoryService(session).update_summary(
            conversation_id=conversation_id,
            user_id=user.id,
            summarizer=SuccessfulSummarizer(),
            summary_version="summary-v1",
            model_version="synthetic-model-v1",
        )
        await session.commit()
        messages = await ConversationService(session).list_messages(
            conversation_id=conversation_id, user_id=user.id, limit=100
        )

    assert summary is not None
    assert summary.source_start_message_id == messages[0].id
    assert summary.source_end_message_id == messages[-1].id
    assert summary.source_message_count == 14
    assert summary.summary_version == "summary-v1"
    assert "旧的浅色偏好" in summary.summary_text
    assert len(messages) == 14


@pytest.mark.asyncio
async def test_summary_failure_keeps_previous_summary_and_original_messages(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, conversation_id = await create_conversation(sessionmaker)
    async with sessionmaker() as session:
        service = ConversationMemoryService(session)
        previous = await service.update_summary(
            conversation_id=conversation_id,
            user_id=user.id,
            summarizer=SuccessfulSummarizer(),
            summary_version="summary-v1",
            model_version="synthetic-model-v1",
        )
        await session.commit()
        failed = await service.update_summary(
            conversation_id=conversation_id,
            user_id=user.id,
            summarizer=FailingSummarizer(),
            summary_version="summary-v2",
            model_version="synthetic-model-v2",
        )
        await session.commit()
        active = await service.get_active_summary(
            conversation_id=conversation_id, user_id=user.id
        )
        message_count = await session.scalar(select(func.count(ConversationMessage.id)))

    assert previous is not None
    assert failed is None
    assert active is not None and active.id == previous.id
    assert active.summary_version == "summary-v1"
    assert message_count == 14


@pytest.mark.asyncio
async def test_blocks_enforce_limits_read_only_and_project_user_isolation(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner, conversation_id = await create_conversation(sessionmaker)
    other, _ = await create_conversation(sessionmaker, "other")
    async with sessionmaker() as session:
        service = ConversationMemoryService(session)
        stable = await service.upsert_block(
            user_id=owner.id,
            block_type="stable_constraints",
            scope_kind="user/global",
            scope_id=None,
            content="永不自动发送邮件",
            read_only=True,
            update_policy="system_only",
        )
        await service.upsert_block(
            user_id=owner.id,
            block_type="project_context",
            scope_kind="user/project",
            scope_id="project-a",
            content="使用 Python 3.12",
        )
        await service.upsert_block(
            user_id=owner.id,
            block_type="project_context",
            scope_kind="user/project",
            scope_id="project-b",
            content="使用 Rust",
        )
        await service.upsert_block(
            user_id=other.id,
            block_type="human_profile",
            scope_kind="user/global",
            scope_id=None,
            content="其他用户内容",
        )
        with pytest.raises(ConversationError, match="memory_block_read_only"):
            await service.upsert_block(
                user_id=owner.id,
                block_type="stable_constraints",
                scope_kind="user/global",
                scope_id=None,
                content="允许自动发送",
            )
        with pytest.raises(ConversationError, match="memory_block_limit_exceeded"):
            await service.upsert_block(
                user_id=owner.id,
                block_type="communication_style",
                scope_kind="user/global",
                scope_id=None,
                content="超出",
                character_limit=1,
            )
        blocks = await service.list_blocks(
            user_id=owner.id,
            conversation_id=conversation_id,
            project_id="project-a",
        )
        await session.commit()

    assert blocks[0].id == stable.id
    assert [item.content for item in blocks] == ["永不自动发送邮件", "使用 Python 3.12"]
    assert all("其他用户" not in item.content for item in blocks)
    assert all(item.scope_id != "project-b" for item in blocks)


def test_v6_short_term_migration_is_linear_and_reversible() -> None:
    import importlib

    migration = importlib.import_module(
        "backend.migrations.versions.202607150005_v6_short_term_memory"
    )
    assert migration.revision == "202607150005"
    assert migration.down_revision == "202607150004"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


@pytest.mark.asyncio
async def test_context_loading_automatically_creates_summary_before_pack(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from memory.working_set import ConversationCompactionPolicy
    from infrastructure.agent_ports import SqlAlchemyConversationContextPort

    user, conversation_id = await create_conversation(sessionmaker)
    async with sessionmaker() as session:
        port = SqlAlchemyConversationContextPort(
            session,
            compaction_policy=ConversationCompactionPolicy(
                trigger_token_threshold=1,
                trigger_message_count=1,
                stale_after_tokens=999,
                stale_after_messages=999,
            ),
        )
        pack = await port.load_context(
            conversation_id=conversation_id,
            user_id=user.id,
            task_id="current-task",
            current_input="继续",
            long_term_memory="",
        )
        await session.commit()
        active = await ConversationMemoryService(session).get_active_summary(
            conversation_id=conversation_id,
            user_id=user.id,
        )
        messages = await ConversationService(session).list_messages(
            conversation_id=conversation_id, user_id=user.id, limit=100
        )

    assert active is not None
    assert active.summary_version == "auto-summary-v1"
    assert active.source_start_message_id == messages[0].id
    assert active.source_end_message_id == messages[-1].id
    assert active.source_message_count == 14
    assert pack.summary == active.summary_text
    summary_trace = next(
        item for item in pack.trace if item["section"] == "conversation_summary"
    )
    assert summary_trace["version"] == "auto-summary-v1"
    assert summary_trace["source_ids"] == (
        active.source_start_message_id,
        active.source_end_message_id,
    )
    assert len(messages) == 14


@pytest.mark.asyncio
async def test_automatic_compaction_reuses_fresh_summary_without_rewrite(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from memory.working_set import ConversationCompactionPolicy
    from infrastructure.agent_ports import SqlAlchemyConversationContextPort

    user, conversation_id = await create_conversation(sessionmaker)
    async with sessionmaker() as session:
        service = ConversationMemoryService(session)
        previous = await service.update_summary(
            conversation_id=conversation_id,
            user_id=user.id,
            summarizer=SuccessfulSummarizer(),
            summary_version="manual-v1",
            model_version="synthetic-model-v1",
        )
        await session.commit()
        assert previous is not None

        port = SqlAlchemyConversationContextPort(
            session,
            compaction_policy=ConversationCompactionPolicy(
                trigger_token_threshold=1,
                trigger_message_count=1,
                stale_after_tokens=1,
                stale_after_messages=1,
            ),
        )
        await port.load_context(
            conversation_id=conversation_id,
            user_id=user.id,
            task_id="current-task",
            current_input="继续",
            long_term_memory="",
        )
        await session.commit()
        active = await service.get_active_summary(
            conversation_id=conversation_id,
            user_id=user.id,
        )
        summary_count = await session.scalar(select(func.count(ConversationSummary.id)))

    assert active is not None
    assert active.id == previous.id
    assert active.summary_version == "manual-v1"
    assert summary_count == 1


@pytest.mark.asyncio
async def test_automatic_compaction_failure_preserves_previous_summary(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from memory.working_set import ConversationCompactionPolicy

    user, conversation_id = await create_conversation(sessionmaker)
    async with sessionmaker() as session:
        service = ConversationMemoryService(session)
        previous = await service.update_summary(
            conversation_id=conversation_id,
            user_id=user.id,
            summarizer=SuccessfulSummarizer(),
            summary_version="manual-v1",
            model_version="synthetic-model-v1",
        )
        await ConversationService(session).append_message(
            conversation_id=conversation_id,
            user_id=user.id,
            role="user",
            content="新增的重要约束",
        )
        await session.commit()
        assert previous is not None

        ensured = await service.ensure_summary_current(
            conversation_id=conversation_id,
            user_id=user.id,
            summarizer=FailingSummarizer(),
            policy=ConversationCompactionPolicy(
                trigger_token_threshold=1,
                trigger_message_count=1,
                stale_after_tokens=1,
                stale_after_messages=1,
            ),
            summary_version="auto-summary-v2",
            model_version="failing-model",
        )
        await session.commit()
        active = await service.get_active_summary(
            conversation_id=conversation_id,
            user_id=user.id,
        )
        message_count = await session.scalar(select(func.count(ConversationMessage.id)))

    assert ensured is not None and ensured.id == previous.id
    assert active is not None and active.id == previous.id
    assert active.summary_version == "manual-v1"
    assert message_count == 15
