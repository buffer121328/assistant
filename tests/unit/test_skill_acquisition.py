from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Sequence
from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent.skill_management.acquisition import (
    SkillAcquisitionDecisionType,
    SkillAcquisitionService,
    SkillCandidate,
)
from agent.skill_management.lifecycle import SkillLifecycleService
from agent.skill_management.store import ManagedSkillStore
from domain.models import Approval, Base, EvolutionChange, SkillAuditLog, User


class FakeMarketplaceProvider:
    name = "curated"
    trust_level = "curated"

    def __init__(self, candidates: Sequence[SkillCandidate] = (), package: bytes = b"") -> None:
        self.candidates = tuple(candidates)
        self.package = package
        self.search_calls: list[str] = []

    async def search(self, *, query: str, tags: Sequence[str] = (), capability_gap: str = "") -> Sequence[SkillCandidate]:
        self.search_calls.append(query)
        return self.candidates

    async def get(self, *, skill_id: str, version: str | None = None):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def download(self, *, skill_id: str, version: str | None = None) -> bytes:
        if not self.package:
            raise RuntimeError("download failed")
        return self.package


def package_bytes(name: str = "market-skill") -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"schema_version": 1, "name": name, "display_name": "Market Skill", "summary": "Market search helper", "version": "1.0.0"}),
        )
        archive.writestr("SKILL.md", "# Market Skill\n\nUse marketplace data safely.\n")
    return buffer.getvalue()


@pytest.fixture
def store(tmp_path: Path) -> ManagedSkillStore:
    builtin = tmp_path / "builtin"
    managed = tmp_path / "managed"
    builtin.mkdir()
    return ManagedSkillStore(builtin_root=builtin, managed_root=managed)


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/skills.db", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def create_user(sessionmaker: async_sessionmaker[AsyncSession]) -> User:
    async with sessionmaker() as session:
        user = User(display_name="Skill User")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def lifecycle(session: AsyncSession, store: ManagedSkillStore, refreshes: list[str]) -> SkillLifecycleService:
    return SkillLifecycleService(session, store=store, refresh_registry=lambda: refreshes.append("refresh"))


@pytest.mark.asyncio
async def test_skill_acquisition_reuses_enabled_local_before_marketplace(sessionmaker, store: ManagedSkillStore) -> None:  # type: ignore[no-untyped-def]
    user = await create_user(sessionmaker)
    provider = FakeMarketplaceProvider([SkillCandidate("remote", "remote", "Remote", "Remote", "1.0.0", "curated", "curated", 1.0)])
    async with sessionmaker() as session:
        refreshes: list[str] = []
        life = lifecycle(session, store, refreshes)
        await life.create(user_id=user.id, name="local-search", display_name="Local Search", summary="python search helper", instructions="Search docs")
        await life.set_enabled(user_id=user.id, name="local-search", enabled=True)
        service = SkillAcquisitionService(lifecycle=life, marketplace_providers=[provider])

        decision = await service.recommend(capability_gap="python search")

    assert decision.decision is SkillAcquisitionDecisionType.ENABLE_EXISTING
    assert decision.local_skill is not None
    assert decision.local_skill.enabled is True
    assert provider.search_calls == []


@pytest.mark.asyncio
async def test_skill_acquisition_prefers_disabled_local_before_marketplace(sessionmaker, store: ManagedSkillStore) -> None:  # type: ignore[no-untyped-def]
    user = await create_user(sessionmaker)
    provider = FakeMarketplaceProvider([SkillCandidate("remote", "remote", "Remote", "Remote", "1.0.0", "curated", "curated", 1.0)])
    async with sessionmaker() as session:
        refreshes: list[str] = []
        life = lifecycle(session, store, refreshes)
        await life.create(user_id=user.id, name="disabled-search", display_name="Disabled Search", summary="python search helper", instructions="Search docs")
        service = SkillAcquisitionService(lifecycle=life, marketplace_providers=[provider])

        decision = await service.recommend(capability_gap="python search")

    assert decision.decision is SkillAcquisitionDecisionType.ENABLE_EXISTING
    assert decision.local_skill is not None
    assert decision.local_skill.enabled is False
    assert decision.requires_approval is True
    assert provider.search_calls == []


@pytest.mark.asyncio
async def test_skill_acquisition_recommends_high_score_marketplace_before_create(sessionmaker, store: ManagedSkillStore) -> None:  # type: ignore[no-untyped-def]
    candidate = SkillCandidate("market", "market", "Market", "browser automation", "1.0.0", "curated", "curated", 0.95)
    provider = FakeMarketplaceProvider([candidate])
    async with sessionmaker() as session:
        service = SkillAcquisitionService(lifecycle=lifecycle(session, store, []), marketplace_providers=[provider])
        decision = await service.recommend(capability_gap="browser automation")

    assert decision.decision is SkillAcquisitionDecisionType.INSTALL_MARKETPLACE
    assert decision.candidates[0].score >= 0.75
    assert decision.requires_approval is True


@pytest.mark.asyncio
async def test_skill_acquisition_low_score_untrusted_moves_to_create_or_compose(sessionmaker, store: ManagedSkillStore) -> None:  # type: ignore[no-untyped-def]
    candidate = SkillCandidate("bad", "bad", "Bad", "unknown", "1.0.0", "untrusted", "untrusted", 0.2).scored()
    provider = FakeMarketplaceProvider([candidate])
    async with sessionmaker() as session:
        service = SkillAcquisitionService(lifecycle=lifecycle(session, store, []), marketplace_providers=[provider])
        composed = await service.recommend(capability_gap="rare thing", composable_tools=["workspace.search_text"])
        created = await service.recommend(capability_gap="rare thing", allow_create=True)
        none = await service.recommend(capability_gap="rare thing", allow_create=False)

    assert composed.decision is SkillAcquisitionDecisionType.COMPOSE_EXISTING
    assert created.decision is SkillAcquisitionDecisionType.CREATE_CANDIDATE
    assert none.decision is SkillAcquisitionDecisionType.NO_SAFE_OPTION


@pytest.mark.asyncio
async def test_marketplace_install_is_disabled_and_audited(sessionmaker, store: ManagedSkillStore) -> None:  # type: ignore[no-untyped-def]
    user = await create_user(sessionmaker)
    async with sessionmaker() as session:
        refreshes: list[str] = []
        service = SkillAcquisitionService(lifecycle=lifecycle(session, store, refreshes))
        item = await service.install_candidate(
            user_id=user.id,
            candidate=SkillCandidate("market", "market-skill", "Market", "summary", "1.0.0", "curated", "curated", 1.0),
            package=package_bytes(),
        )
        audits = list((await session.scalars(select(SkillAuditLog))).all())

    assert item.name == "market-skill"
    assert item.enabled is False
    assert audits[-1].action == "install"
    assert audits[-1].status == "succeeded"
    assert refreshes


@pytest.mark.asyncio
async def test_propose_create_creates_evolution_change_and_approval_not_managed_root(sessionmaker, store: ManagedSkillStore) -> None:  # type: ignore[no-untyped-def]
    user = await create_user(sessionmaker)
    async with sessionmaker() as session:
        task = __import__("domain.models", fromlist=["Task"]).Task(user_id=user.id, platform="api", task_type="agent", input_text="need skill")
        session.add(task)
        await session.flush()
        service = SkillAcquisitionService(lifecycle=lifecycle(session, store, []))
        change = await service.propose_create(session=session, task_id=task.id, user_id=user.id, name="new-skill", instructions="Do things", evidence="No safe option")
        approvals = list((await session.scalars(select(Approval))).all())
        changes = list((await session.scalars(select(EvolutionChange))).all())

    assert change.id == changes[0].id
    assert approvals[0].tool_name == "skills.propose_create"
    assert not (store.managed_root / "new-skill").exists()
