from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domain.models import Base, Memory, Task, TaskStatus, User
from agent import (
    AgentHarness,
    CapabilitiesBuilder,
    CapabilitySnapshot,
    ContextBuilder,
    DefaultPlanningLayer,
    DefaultProfileSelector,
    ExecutionOutcome,
    ExecutionPlan,
    SkillDefinition,
    SkillsLoader,
    TaskContext,
    ToolCapability,
    UnsupportedModelClassError,
)
from tools import NormalizedSearchSource, SearchWebResult


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v2-planning.db",
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def make_task(
    task_type: str,
    *,
    model_class: str | None = None,
    input_text: str | None = None,
) -> Task:
    return Task(
        id=f"task-{task_type}",
        user_id="user-1",
        platform="api",
        task_type=task_type,
        input_text=input_text or f"/{task_type} 验收 V2 规划层",
        status=TaskStatus.PENDING.value,
        model_class=model_class,
    )


def test_01_task_types_select_distinct_v2_profiles_and_reject_unknown_model() -> None:
    selector = DefaultProfileSelector()

    selected = {
        task_type: selector.select(make_task(task_type))
        for task_type in ("plan", "learn", "daily", "office")
    }

    assert selected["plan"].name == "v2.planner"
    assert selected["learn"].name == "v2.researcher"
    assert selected["daily"].name == "v2.daily_reporter"
    assert selected["office"].name == "v2.office_writer"
    assert len({profile.name for profile in selected.values()}) == 4
    assert all(profile.executor_kind == "langgraph" for profile in selected.values())

    with pytest.raises(UnsupportedModelClassError, match="Unsupported model class"):
        selector.select(make_task("plan", model_class="retired-provider"))


def test_02_skills_loader_reads_only_explicit_local_skills(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    search_dir = skills_root / "search"
    undeclared_dir = skills_root / "undeclared"
    search_dir.mkdir(parents=True)
    undeclared_dir.mkdir(parents=True)
    (search_dir / "SKILL.md").write_text(
        "# Search\n\nUse trusted search sources.",
        encoding="utf-8",
    )
    (undeclared_dir / "SKILL.md").write_text(
        "# Undeclared\n\nThis must not load automatically.",
        encoding="utf-8",
    )

    skills = SkillsLoader(skills_root).load(("search",))

    assert [skill.name for skill in skills] == ["search"]
    assert "trusted search sources" in skills[0].instructions
    assert all(skill.name != "undeclared" for skill in skills)


def test_03_capabilities_builder_excludes_disabled_and_unknown_tools() -> None:
    builder = CapabilitiesBuilder(
        (
            ToolCapability(
                name="search.web",
                description="Search public web sources",
                enabled=True,
            ),
            ToolCapability(
                name="shell.exec",
                description="Execute a local shell command",
                enabled=False,
                approval_required=True,
            ),
            ToolCapability(
                name="email.send",
                description="Send an email after approval",
                enabled=True,
                approval_required=True,
            ),
        )
    )

    snapshot = builder.build(
        requested_tools=("search.web", "shell.exec", "email.send", "unknown.tool")
    )

    assert snapshot.allowed_tools == ("search.web",)
    assert snapshot.approval_required_tools == ("email.send",)
    assert "shell.exec" not in snapshot.allowed_tools
    assert "unknown.tool" not in snapshot.allowed_tools


def test_04_context_builder_includes_user_memory_skills_and_capabilities() -> None:
    user = User(id="user-1", display_name="V2 User")
    task = make_task("learn", input_text="/learn LangGraph planning context")
    skills = (
        SkillDefinition(
            name="search",
            instructions="# Search\n\nPrefer primary sources.",
            source="backend/resources/skillpacks/search/SKILL.md",
        ),
    )
    capabilities = CapabilitySnapshot(
        allowed_tools=("search.web",),
        approval_required_tools=(),
        summaries=("search.web: Search public web sources",),
    )

    context = ContextBuilder().build(
        task=task,
        user=user,
        memory_summary="先给结论，再列来源。",
        skills=skills,
        capabilities=capabilities,
    )

    assert context.task_id == task.id
    assert context.user_id == user.id
    assert context.user_display_name == "V2 User"
    assert context.memory_summary == "先给结论，再列来源。"
    assert context.skill_names == ("search",)
    assert "Prefer primary sources" in context.skill_instructions[0]
    assert context.allowed_tools == ("search.web",)
    assert context.capability_summary == capabilities.summaries


def test_05_planner_returns_bounded_execution_plan() -> None:
    task = make_task("learn", input_text="/learn ExecutionPlan 边界")
    profile = DefaultProfileSelector().select(task)
    context = TaskContext(
        task_id=task.id,
        user_id=task.user_id,
        task_type=task.task_type,
        input_text=task.input_text,
        memory_summary="",
        allowed_tools=("search.web",),
        capability_summary=("search.web: Search public web sources",),
    )

    plan = DefaultPlanningLayer().build_plan(
        task=task,
        profile=profile,
        context=context,
    )

    assert plan.goal == "ExecutionPlan 边界"
    assert plan.steps
    assert plan.allowed_tools == ("search.web",)
    assert plan.approval_required_tools == ()
    assert plan.risk_level in {"low", "medium", "high"}
    assert 0 < plan.max_steps <= 12
    assert 0 < plan.timeout_seconds <= 300
    assert plan.profile_name == "v2.researcher"


class RecordingPlanner:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.delegate = DefaultPlanningLayer()

    def build_plan(
        self,
        *,
        task: Task,
        profile: Any,
        context: TaskContext,
    ) -> ExecutionPlan:
        self.events.append("plan")
        return self.delegate.build_plan(task=task, profile=profile, context=context)


class FakeSearchTool:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def search(
        self, *, task_id: str, user_id: str, query: str
    ) -> SearchWebResult:
        self.events.append("search")
        return SearchWebResult(
            query=query,
            sources=[
                NormalizedSearchSource(
                    title="Primary source",
                    url="https://example.invalid/source",
                    snippet="Planning context source",
                    provider_metadata={},
                ),
            ],
        )


class FakeExecutionBoundary:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> ExecutionOutcome:
        self.events.append("execute")
        self.calls.append(kwargs)
        return ExecutionOutcome(
            status=TaskStatus.SUCCESS.value,
            result_text="V2 planning execution result",
            workflow_key=kwargs["plan"].workflow_key,
        )


@pytest.mark.asyncio
async def test_06_harness_plans_before_primary_executor_manages_tools(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = User(display_name="Harness User")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type="learn",
            input_text="/learn V2 planning integration",
            status=TaskStatus.PENDING.value,
        )
        session.add_all([task, Memory(user_id=user.id, content="回答先给结论")])
        await session.commit()

        events: list[str] = []
        boundary = FakeExecutionBoundary(events)
        result = await AgentHarness(
            session=session,
            executor=boundary,
            search_tool=FakeSearchTool(events),
            planning_layer=RecordingPlanner(events),
        ).execute_task(task.id)

    assert result.status == TaskStatus.SUCCESS.value
    assert events == ["plan", "execute"]
    assert len(boundary.calls) == 1
    plan = boundary.calls[0]["plan"]
    context = boundary.calls[0]["context"]
    assert plan.allowed_tools == ("search.web",)
    assert context.memory_summary == ""
    assert context.skill_names == ("research",)
    assert context.sources == ()


def test_07_readme_preserves_v2_02_planning_layer_details() -> None:
    readme = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")

    assert "v2.planner" in readme
    assert "v2.researcher" in readme
    assert "backend/resources/skillpacks/*/SKILL.md" in readme
    assert "不会自动启用" in readme
