from __future__ import annotations

from collections.abc import AsyncIterator
from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from assistant_api.models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    Base,
    EvolutionChange,
    EvolutionVersion,
    Task,
    User,
)
from packages.agent_harness.governed_evolution import (
    EvolutionStaleError,
    EvolutionValidationError,
    GovernedEvolutionService,
)
from packages.agent_harness.skill_store import (
    ManagedSkillNotFoundError,
    ManagedSkillStore,
)


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v4-evolution.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_evolution_requires_exact_change_approval_and_supports_rollback(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    prompt_root = tmp_path / "managed-prompts"
    skill_root = tmp_path / "managed-skills"
    prompt_root.mkdir()
    target = prompt_root / "assistant.md"
    target.write_text("旧提示\n", encoding="utf-8")

    async with sessionmaker() as session:
        user = User(display_name="evolution")
        session.add(user)
        await session.flush()
        task = Task(user_id=user.id, platform="api", task_type="agent", input_text="优化提示", status="waiting_approval")
        session.add(task)
        await session.commit()
        service = GovernedEvolutionService(session=session, prompt_root=prompt_root, skill_root=skill_root)
        change = await service.propose(
            task_id=task.id,
            user_id=user.id,
            target_kind="prompt",
            target_name="assistant.md",
            candidate_content="新提示：保持简洁。\n",
            evidence="deterministic regression passed",
        )
        assert target.read_text(encoding="utf-8") == "旧提示\n"
        approval = await session.scalar(select(Approval).where(Approval.subject == change.id))
        assert approval is not None
        assert approval.approval_type == ApprovalType.CHANGE.value
        approval.status = ApprovalStatus.APPROVED.value
        approval.decided_by_user_id = user.id
        await session.commit()

        applied = await service.apply(change_id=change.id, user_id=user.id)
        assert applied.status == "applied"
        assert target.read_text(encoding="utf-8") == "新提示：保持简洁。\n"
        rolled_back = await service.rollback(change_id=change.id, user_id=user.id)
        assert rolled_back.status == "rolled_back"
        assert target.read_text(encoding="utf-8") == "旧提示\n"
        versions = list(await session.scalars(select(EvolutionVersion).where(EvolutionVersion.change_id == change.id)))
        assert [item.action for item in versions] == ["apply", "rollback"]


@pytest.mark.asyncio
async def test_evolution_rejects_unsafe_target_and_stale_base(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    prompt_root = tmp_path / "managed-prompts"
    skill_root = tmp_path / "managed-skills"
    prompt_root.mkdir()
    target = prompt_root / "assistant.md"
    target.write_text("base", encoding="utf-8")
    async with sessionmaker() as session:
        user = User(display_name="evolution")
        session.add(user)
        await session.flush()
        task = Task(user_id=user.id, platform="api", task_type="agent", input_text="优化", status="waiting_approval")
        session.add(task)
        await session.commit()
        service = GovernedEvolutionService(session=session, prompt_root=prompt_root, skill_root=skill_root)
        with pytest.raises(EvolutionValidationError):
            await service.propose(task_id=task.id, user_id=user.id, target_kind="prompt", target_name="../source.py", candidate_content="bad", evidence="x")

        change = await service.propose(task_id=task.id, user_id=user.id, target_kind="prompt", target_name="assistant.md", candidate_content="candidate", evidence="x")
        approval = await session.scalar(select(Approval).where(Approval.subject == change.id))
        assert approval is not None
        approval.status = ApprovalStatus.APPROVED.value
        approval.decided_by_user_id = user.id
        await session.commit()
        target.write_text("externally changed", encoding="utf-8")
        with pytest.raises(EvolutionStaleError):
            await service.apply(change_id=change.id, user_id=user.id)
        stored = await session.get(EvolutionChange, change.id)
        assert stored is not None and stored.status == "stale"
        assert target.read_text(encoding="utf-8") == "externally changed"


@pytest.mark.asyncio
async def test_approved_local_skill_package_installs_disabled_and_rolls_back(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    builtin_root = tmp_path / "builtin"
    managed_root = tmp_path / "managed"
    package_root = tmp_path / "packages"
    prompt_root = tmp_path / "prompts"
    for directory in (builtin_root, managed_root, package_root, prompt_root):
        directory.mkdir()
    package = package_root / "review-helper.zip"
    package.write_bytes(_skill_package("review-helper"))
    store = ManagedSkillStore(builtin_root=builtin_root, managed_root=managed_root)

    async with sessionmaker() as session:
        user = User(display_name="skill evolution")
        session.add(user)
        await session.flush()
        task = Task(user_id=user.id, platform="api", task_type="agent", input_text="安装 Skill", status="waiting_approval")
        session.add(task)
        await session.commit()
        service = GovernedEvolutionService(
            session=session,
            prompt_root=prompt_root,
            skill_root=managed_root,
            skill_store=store,
            skill_package_root=package_root,
        )
        change = await service.propose_skill_install(
            task_id=task.id,
            user_id=user.id,
            package_name=package.name,
            evidence="package validation passed",
        )
        approval = await session.scalar(select(Approval).where(Approval.subject == change.id))
        assert approval is not None
        approval.status = ApprovalStatus.APPROVED.value
        approval.decided_by_user_id = user.id
        await session.commit()

        await service.apply(change_id=change.id, user_id=user.id)
        assert store.get("review-helper").enabled is False
        await service.rollback(change_id=change.id, user_id=user.id)
        with pytest.raises(ManagedSkillNotFoundError):
            store.get("review-helper")


def _skill_package(name: str) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "name": name,
                    "display_name": "Review Helper",
                    "summary": "Review generated answers",
                    "version": "1.0.0",
                }
            ),
        )
        archive.writestr("SKILL.md", "# Review Helper\n\nReview generated answers.\n")
    return stream.getvalue()
