from __future__ import annotations

from collections.abc import AsyncIterator
import importlib
import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from application.memory_release import (
    MemoryReleaseError,
    MemoryReleaseService,
    default_policy_config,
)
from domain.models import (
    Base,
    MemoryEffectiveness,
    MemoryReleaseReport,
    MemoryRetrievalPolicyVersion,
    User,
)
from application.services import MemoryService, TaskServiceError
from evaluation.memory_release import evaluate_memory_release_fixture
from memory.context import load_memory_context


DATASET = Path(__file__).parents[1] / "evals/datasets/memory_release_v6_07.json"


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/memory-release.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_effectiveness_is_owner_scoped_and_evidence_idempotent(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        owner = User(display_name="owner")
        other = User(display_name="other")
        session.add_all((owner, other))
        await session.flush()
        memory = await MemoryService(session).create_memory(
            user_id=owner.id, content="回答先给结论"
        )
        other_memory = await MemoryService(session).create_memory(
            user_id=other.id, content="其他用户偏好"
        )
        service = MemoryReleaseService(session)
        first = await service.record_effectiveness(
            user_id=owner.id,
            memory_id=memory.id,
            evidence_key="feedback-1",
            feedback_type="helpful",
            outcome="success",
        )
        repeated = await service.record_effectiveness(
            user_id=owner.id,
            memory_id=memory.id,
            evidence_key="feedback-1",
            feedback_type="helpful",
            outcome="success",
        )
        with pytest.raises(TaskServiceError):
            await service.record_effectiveness(
                user_id=owner.id,
                memory_id=other_memory.id,
                evidence_key="foreign-feedback",
                feedback_type="harmful",
            )
        await session.commit()
        rows = list(await session.scalars(select(MemoryEffectiveness)))

    assert first.id == repeated.id
    assert first.helpful_count == 1 and first.success_count == 1
    assert [(row.user_id, row.memory_id) for row in rows] == [(owner.id, memory.id)]


@pytest.mark.asyncio
async def test_shadow_policy_requires_passing_owned_report_and_rolls_back(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        owner = User(display_name="owner")
        other = User(display_name="other")
        session.add_all((owner, other))
        await session.flush()
        service = MemoryReleaseService(session)
        config_v1 = default_policy_config()
        active = await service.bootstrap_active_policy(
            user_id=owner.id, version="v1", config=config_v1
        )
        config_v2 = dict(config_v1)
        config_v2["semantic"] = 0.35
        config_v2["keyword"] = 0.10
        shadow = await service.create_shadow_policy(
            user_id=owner.id, version="v2", config=config_v2
        )
        comparison = await service.compare_shadow(
            user_id=owner.id,
            shadow_policy_id=shadow.id,
            active_memory_ids=("memory-a", "memory-b"),
            shadow_memory_ids=("memory-b", "memory-a"),
        )
        still_active = await service.get_active_policy(
            user_id=owner.id, scope_key="user/global:"
        )

        pending_report = await service.persist_release_report(
            user_id=owner.id,
            policy_version="v2",
            report=evaluate_memory_release_fixture(DATASET),
        )
        with pytest.raises(MemoryReleaseError, match="report is invalid"):
            await service.activate_policy(
                user_id=owner.id,
                policy_id=shadow.id,
                report_id=pending_report.id,
                approved=True,
            )

        passing = evaluate_memory_release_fixture(DATASET)
        passing.update(
            passed=True,
            manual_evidence_complete=True,
            gate_reasons=[],
        )
        report = await service.persist_release_report(
            user_id=owner.id, policy_version="v2", report=passing
        )
        with pytest.raises(MemoryReleaseError, match="requires approval"):
            await service.activate_policy(
                user_id=owner.id,
                policy_id=shadow.id,
                report_id=report.id,
                approved=False,
            )
        with pytest.raises(MemoryReleaseError, match="not found"):
            await service.activate_policy(
                user_id=other.id,
                policy_id=shadow.id,
                report_id=report.id,
                approved=True,
            )
        activated = await service.activate_policy(
            user_id=owner.id,
            policy_id=shadow.id,
            report_id=report.id,
            approved=True,
        )
        rolled_back = await service.rollback_policy(
            user_id=owner.id, policy_id=activated.id
        )
        await session.commit()
        policies = list(
            await session.scalars(
                select(MemoryRetrievalPolicyVersion).order_by(
                    MemoryRetrievalPolicyVersion.version
                )
            )
        )
        reports = list(await session.scalars(select(MemoryReleaseReport)))

    assert comparison.active_version == "v1"
    assert comparison.shadow_version == "v2"
    assert comparison.changed_positions == 2
    assert still_active is not None and still_active.id == active.id
    assert rolled_back.id == active.id and rolled_back.status == "active"
    assert [(item.version, item.status) for item in policies] == [
        ("v1", "active"),
        ("v2", "rolled_back"),
    ]
    assert len(reports) == 2
    assert all("回答先给结论" not in item.metrics_json for item in reports)


@pytest.mark.asyncio
async def test_context_uses_only_active_policy_and_caps_policy_limit(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        owner = User(display_name="owner")
        session.add(owner)
        await session.flush()
        for index in range(3):
            await MemoryService(session).create_memory(
                user_id=owner.id, content=f"项目偏好 {index}"
            )
        service = MemoryReleaseService(session)
        active_config = default_policy_config()
        active_config["max_items"] = 1
        await service.bootstrap_active_policy(
            user_id=owner.id, version="v1", config=active_config
        )
        shadow_config = default_policy_config()
        shadow_config["max_items"] = 8
        shadow = await service.create_shadow_policy(
            user_id=owner.id, version="v2", config=shadow_config
        )
        before = await load_memory_context(
            session=session, user_id=owner.id, query="项目偏好", semantic_limit=2
        )
        passing = evaluate_memory_release_fixture(DATASET)
        passing.update(passed=True, manual_evidence_complete=True, gate_reasons=[])
        report = await service.persist_release_report(
            user_id=owner.id, policy_version="v2", report=passing
        )
        await service.activate_policy(
            user_id=owner.id,
            policy_id=shadow.id,
            report_id=report.id,
            approved=True,
        )
        after = await load_memory_context(
            session=session, user_id=owner.id, query="项目偏好", semantic_limit=2
        )

    assert len(before.items) == 1
    assert len(after.items) == 2


def test_manual_evidence_completion_changes_final_release_only(tmp_path: Path) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["manual_evidence"]["provided"] = payload["manual_evidence"]["required"]
    path = tmp_path / "complete.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = evaluate_memory_release_fixture(path)

    assert report["automated_passed"] is True
    assert report["manual_evidence_complete"] is True
    assert report["passed"] is True
    assert report["gate_reasons"] == []


def test_v6_release_migration_and_backup_contract() -> None:
    from scripts.ops.db_common import COUNTED_TABLES

    migration = importlib.import_module(
        "backend.migrations.versions.202607160004_v6_adaptive_memory_release"
    )
    assert migration.revision == "202607160004"
    assert migration.down_revision == "202607160003"
    assert callable(migration.upgrade) and callable(migration.downgrade)
    assert {
        "memory_release_reports",
        "memory_retrieval_policy_versions",
        "memory_effectiveness",
        "memory_effectiveness_events",
    }.issubset(COUNTED_TABLES)
