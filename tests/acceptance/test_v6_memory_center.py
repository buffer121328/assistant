from __future__ import annotations
from collections.abc import AsyncIterator
from pathlib import Path
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from infrastructure.config import Settings
from app.main import create_app
from domain.models import Base, User
from domain.services import MemoryService


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/memory-center.db", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def client(sessionmaker: async_sessionmaker[AsyncSession]) -> TestClient:
    app = create_app(Settings(database_url="sqlite+aiosqlite:///unused.db"))
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


async def users(sessionmaker: async_sessionmaker[AsyncSession]) -> tuple[User, User]:
    async with sessionmaker() as session:
        first = User(display_name="owner")
        second = User(display_name="other")
        session.add_all((first, second))
        await session.commit()
        return first, second


@pytest.mark.asyncio
async def test_memory_center_overview_list_detail_and_owner_isolation(
    client: TestClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    owner, other = await users(sessionmaker)
    async with sessionmaker() as session:
        active = await MemoryService(session).create_memory(
            user_id=owner.id, content="回答先给结论"
        )
        candidate = await MemoryService(session).create_memory(
            user_id=owner.id, content="候选格式", confirmed_by_user=False
        )
        await session.commit()
        active_id = active.id
        candidate_id = candidate.id
    overview = client.get("/api/memories/overview", params={"user_id": owner.id})
    listed = client.get(
        "/api/memories", params={"user_id": owner.id, "status": "candidate"}
    )
    detail = client.get(f"/api/memories/{active_id}", params={"user_id": owner.id})
    denied = client.get(f"/api/memories/{active_id}", params={"user_id": other.id})
    assert overview.json()["counts"] == {"active": 1, "candidate": 1}
    assert [item["memory_id"] for item in listed.json()["items"]] == [candidate_id]
    assert detail.json()["memory"]["source_kind"] == "explicit_service"
    assert "reason_code" in detail.json()["memory"]
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_memory_actions_refresh_server_state_only_after_success(
    client: TestClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    owner, other = await users(sessionmaker)
    async with sessionmaker() as session:
        candidate = await MemoryService(session).create_memory(
            user_id=owner.id, content="候选", confirmed_by_user=False
        )
        await session.commit()
        memory_id = candidate.id
    denied = client.post(
        f"/api/memories/{memory_id}/actions/confirm", json={"user_id": other.id}
    )
    confirmed = client.post(
        f"/api/memories/{memory_id}/actions/confirm", json={"user_id": owner.id}
    )
    pinned = client.post(
        f"/api/memories/{memory_id}/actions/pin", json={"user_id": owner.id}
    )
    scoped = client.post(
        f"/api/memories/{memory_id}/actions/scope",
        json={
            "user_id": owner.id,
            "scope_kind": "user/project",
            "scope_id": "project-x",
        },
    )
    assert denied.status_code == 404
    assert confirmed.json()["memory"]["status"] == "active"
    assert pinned.json()["memory"]["is_pinned"] is True
    assert scoped.json()["memory"]["scope_id"] == "project-x"


@pytest.mark.asyncio
async def test_memory_detail_exposes_owned_links_feedback_usage_without_foreign_ids(
    client: TestClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from domain.models import (
        MemoryLink,
        MemoryRetrievalTrace,
        MemoryRetrievalTraceItem,
        Task,
        TaskStatus,
    )

    owner, other = await users(sessionmaker)
    async with sessionmaker() as session:
        service = MemoryService(session)
        source = await service.create_memory(user_id=owner.id, content="先给结论")
        target = await service.create_memory(user_id=owner.id, content="使用中文")
        foreign = await service.create_memory(user_id=other.id, content="其他用户内容")
        await service.add_link(
            user_id=owner.id,
            source_memory_id=source.id,
            target_memory_id=target.id,
            link_type="related_to",
        )
        session.add(
            MemoryLink(
                source_memory_id=source.id,
                target_memory_id=foreign.id,
                link_type="supports",
                created_by="synthetic-test",
            )
        )
        await service.add_feedback(
            user_id=owner.id, memory_id=source.id, feedback_type="helpful"
        )
        task = Task(
            user_id=owner.id,
            platform="api",
            task_type="agent",
            input_text="给出建议",
            status=TaskStatus.SUCCESS.value,
        )
        session.add(task)
        await session.flush()
        trace = MemoryRetrievalTrace(
            user_id=owner.id,
            task_id=task.id,
            query_hash="synthetic-query-hash",
            retrieval_mode="keyword",
            time_intent="current",
            candidate_count=1,
            injected_count=1,
            injected_tokens=4,
            latency_ms=1.0,
        )
        session.add(trace)
        await session.flush()
        session.add(
            MemoryRetrievalTraceItem(
                trace_id=trace.id,
                memory_id=source.id,
                filter_reason="ranked",
                component_scores_json="{}",
                final_score=1.0,
                final_rank=1,
                injected_tokens=4,
            )
        )
        await session.commit()
        source_id = source.id
        target_id = target.id
        foreign_id = foreign.id

    response = client.get(
        f"/api/memories/{source_id}", params={"user_id": owner.id}
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["memory"]["source_kind"] == "explicit_service"
    assert payload["links"] == [
        {
            "source_memory_id": source_id,
            "target_memory_id": target_id,
            "link_type": "related_to",
            "confidence": 1.0,
            "created_by": "user",
        }
    ]
    assert payload["feedback"][0]["feedback_type"] == "helpful"
    assert payload["usage"][0] == {
        "trace_id": trace.id,
        "filter_reason": "ranked",
        "final_rank": 1,
        "injected_tokens": 4,
    }
    assert foreign_id not in response.text


@pytest.mark.asyncio
async def test_complete_memory_actions_create_policy_retrieval_and_digest_are_owned(
    client: TestClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from datetime import UTC, datetime, timedelta

    from domain.models import (
        Memory,
        MemoryConsolidationDigest,
        MemoryIndexOutbox,
        MemoryRetrievalTrace,
        Task,
        TaskStatus,
    )
    from sqlalchemy import select

    owner, other = await users(sessionmaker)
    created = client.post(
        "/api/memories",
        json={
            "user_id": owner.id,
            "content": "回答使用清单",
            "memory_type": "preference",
        },
    )
    assert created.status_code == 201
    active_id = created.json()["memory"]["memory_id"]

    async with sessionmaker() as session:
        service = MemoryService(session)
        to_confirm = await service.create_memory(
            user_id=owner.id, content="候选确认", confirmed_by_user=False
        )
        to_reject = await service.create_memory(
            user_id=owner.id, content="候选拒绝", confirmed_by_user=False
        )
        to_archive = await service.create_memory(user_id=owner.id, content="待归档")
        to_forget = await service.create_memory(user_id=owner.id, content="待忘记")
        task = Task(
            user_id=owner.id,
            platform="api",
            task_type="agent",
            input_text="synthetic",
            status=TaskStatus.SUCCESS.value,
        )
        session.add(task)
        await session.flush()
        trace = MemoryRetrievalTrace(
            user_id=owner.id,
            task_id=task.id,
            query_hash="owned-query-hash",
            retrieval_mode="keyword",
            time_intent="current",
            candidate_count=0,
            injected_count=0,
            injected_tokens=0,
            latency_ms=0.5,
        )
        digest = MemoryConsolidationDigest(
            user_id=owner.id,
            digest_type="daily",
            window_start=datetime.now(UTC) - timedelta(days=1),
            window_end=datetime.now(UTC),
            content_json='{"summary": "synthetic"}',
        )
        session.add_all((trace, digest))
        await session.commit()
        confirm_id = to_confirm.id
        reject_id = to_reject.id
        archive_id = to_archive.id
        forget_id = to_forget.id
        task_id = task.id

    def action(memory_id: str, name: str, **values: object):
        return client.post(
            f"/api/memories/{memory_id}/actions/{name}",
            json={"user_id": owner.id, **values},
        )

    assert action(confirm_id, "confirm").json()["memory"]["status"] == "active"
    assert action(reject_id, "reject").json()["memory"]["status"] == "rejected"
    assert action(active_id, "pin").json()["memory"]["is_pinned"] is True
    assert action(active_id, "unpin").json()["memory"]["is_pinned"] is False
    assert action(
        active_id,
        "scope",
        scope_kind="user/project",
        scope_id="project-v6",
    ).json()["memory"]["scope_id"] == "project-v6"
    assert action(
        active_id,
        "validity",
        valid_from="2026-07-01T00:00:00+00:00",
        valid_to="2026-08-01T00:00:00+00:00",
    ).status_code == 200
    invalid_validity = action(
        active_id,
        "validity",
        valid_from="2026-09-01T00:00:00+00:00",
        valid_to="2026-08-01T00:00:00+00:00",
    )
    assert invalid_validity.status_code == 400
    corrected = action(active_id, "correct", content="回答使用编号清单")
    corrected_id = corrected.json()["memory"]["memory_id"]
    assert corrected.json()["memory"]["status"] == "active"
    assert action(corrected_id, "rebuild-index").status_code == 200
    assert action(archive_id, "archive").json()["memory"]["status"] == "archived"
    assert action(forget_id, "forget").json()["memory"]["status"] == "deleted"
    assert client.post(
        f"/api/memories/{corrected_id}/actions/pin",
        json={"user_id": other.id},
    ).status_code == 404

    policy = client.put(
        "/api/memory/policies/never_remember:reflection",
        json={"user_id": owner.id, "enabled": True},
    )
    denied_policy = client.put(
        "/api/memory/policies/never_remember:reflection",
        json={"user_id": "missing-user", "enabled": True},
    )
    policies = client.get("/api/memory/policies", params={"user_id": owner.id})
    retrieval = client.get(
        f"/api/tasks/{task_id}/memory-retrieval", params={"user_id": owner.id}
    )
    denied_retrieval = client.get(
        f"/api/tasks/{task_id}/memory-retrieval", params={"user_id": other.id}
    )
    digests = client.get(
        "/api/memory/consolidation-digests", params={"user_id": owner.id}
    )
    denied_digests = client.get(
        "/api/memory/consolidation-digests", params={"user_id": "missing-user"}
    )

    assert policy.json()["policy"]["enabled"] is True
    assert denied_policy.status_code == 404
    assert policies.json()["items"][0]["policy_key"] == "never_remember:reflection"
    assert retrieval.status_code == 200 and retrieval.json()["trace"]["trace_id"] == trace.id
    assert denied_retrieval.status_code == 404
    assert digests.status_code == 200 and len(digests.json()["items"]) == 1
    assert denied_digests.status_code == 404

    async with sessionmaker() as session:
        original = await session.get(Memory, active_id)
        outbox = list(
            await session.scalars(
                select(MemoryIndexOutbox).where(
                    MemoryIndexOutbox.memory_id == corrected_id,
                    MemoryIndexOutbox.operation == "rebuild",
                )
            )
        )
    assert original is not None and original.status == "superseded"
    assert len(outbox) == 1


@pytest.mark.asyncio
async def test_langbot_aliases_why_policy_and_sensitive_list_are_safe(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from domain.memory_candidates import MemoryPolicyService
    from domain.models import MemoryRetrievalTrace, Task, TaskStatus

    owner, other = await users(sessionmaker)
    async with sessionmaker() as session:
        service = MemoryService(session)
        sensitive = await service.create_memory(
            user_id=owner.id, content="synthetic private preference"
        )
        sensitive.sensitivity = "sensitive"
        owned_task = Task(
            user_id=owner.id,
            platform="api",
            task_type="agent",
            input_text="owned task",
            status=TaskStatus.SUCCESS.value,
        )
        foreign_task = Task(
            user_id=other.id,
            platform="api",
            task_type="agent",
            input_text="foreign task",
            status=TaskStatus.SUCCESS.value,
        )
        session.add_all((owned_task, foreign_task))
        await session.flush()
        session.add(
            MemoryRetrievalTrace(
                user_id=owner.id,
                task_id=owned_task.id,
                query_hash="safe-query-hash",
                retrieval_mode="hybrid",
                time_intent="current",
                candidate_count=2,
                injected_count=1,
                injected_tokens=8,
                latency_ms=2.0,
            )
        )
        await MemoryPolicyService(session).set_never_remember(
            user_id=owner.id, memory_type="reflection"
        )
        await session.commit()

        async def run(command: str) -> Task:
            task = Task(
                user_id=owner.id,
                platform="langbot",
                task_type="memory",
                input_text=f"/memory {command}",
                status=TaskStatus.PENDING.value,
            )
            session.add(task)
            await session.commit()
            return await MemoryService(session).execute_task(task.id)

        remembered = await run("remember 回答前先核对来源")
        listed = await run("list preference")
        why = await run(f"why {owned_task.id}")
        denied = await run(f"why {foreign_task.id}")
        policy = await run("policy")

    assert remembered.status == "success"
    assert "synthetic private preference" not in (listed.result_text or "")
    assert "[SENSITIVE]" in (listed.result_text or "")
    assert "使用了 1 条记忆" in (why.result_text or "")
    assert "hybrid" in (why.result_text or "")
    assert denied.status == "failed"
    assert foreign_task.id not in (denied.error_message or "")
    assert "never_remember:reflection" in (policy.result_text or "")
