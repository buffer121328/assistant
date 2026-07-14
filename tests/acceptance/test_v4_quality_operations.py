from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from assistant_api.models import Base, Task, User
from packages.observability import NoopObservation
from packages.quality import (
    JudgeDecision,
    JudgeRequest,
    QualityEvaluator,
    SamplingPolicy,
)


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v4-quality.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class Judge:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[JudgeRequest] = []

    async def evaluate(self, request: JudgeRequest) -> JudgeDecision:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("judge unavailable")
        return JudgeDecision(relevance=0.9, completeness=0.4, faithfulness=0.8, rationale="bounded")


class Observability:
    def __init__(self) -> None:
        self.scores: list[dict[str, Any]] = []

    @contextmanager
    def observe(self, *args: Any, **kwargs: Any) -> Iterator[NoopObservation]:
        del args, kwargs
        yield NoopObservation()

    def score(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


def test_sampling_policy_is_stable_and_zero_disables_all() -> None:
    assert SamplingPolicy(rate=0).should_sample("task-1") is False
    assert SamplingPolicy(rate=1).should_sample("task-1") is True
    policy = SamplingPolicy(rate=0.5, version="judge-v1")
    assert policy.should_sample("task-stable") == policy.should_sample("task-stable")


@pytest.mark.asyncio
async def test_quality_evaluation_is_idempotent_scores_and_keeps_task_success(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    judge = Judge()
    observability = Observability()
    async with sessionmaker() as session:
        user = User(display_name="quality")
        session.add(user)
        await session.flush()
        task = Task(user_id=user.id, platform="api", task_type="agent", input_text="问题", status="success", result_text="答案")
        session.add(task)
        await session.commit()
        evaluator = QualityEvaluator(
            sampling=SamplingPolicy(rate=1, version="judge-v1"),
            judge=judge,
            observability=observability,
            threshold=0.6,
        )
        first = await evaluator.evaluate_task(session=session, task=task)
        second = await evaluator.evaluate_task(session=session, task=task)
        await session.refresh(task)

    assert first is not None and first.completeness == 0.4
    assert second == first
    assert len(judge.requests) == 1
    assert [item["name"] for item in observability.scores] == [
        "judge.relevance",
        "judge.completeness",
        "judge.faithfulness",
    ]
    assert task.status == "success"


@pytest.mark.asyncio
async def test_judge_failure_is_best_effort(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = User(display_name="quality")
        session.add(user)
        await session.flush()
        task = Task(user_id=user.id, platform="api", task_type="agent", input_text="问题", status="success", result_text="答案")
        session.add(task)
        await session.commit()
        result = await QualityEvaluator(
            sampling=SamplingPolicy(rate=1),
            judge=Judge(fail=True),
            observability=Observability(),
        ).evaluate_task(session=session, task=task)
        await session.refresh(task)

    assert result is None
    assert task.status == "success"
