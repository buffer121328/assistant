from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import pytest
import pytest_asyncio
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from assistant_api.models import Approval, ApprovalType, Base, Task, User
from assistant_api.services import TaskService
from packages.agent_harness import (
    AgentDecision,
    AgentModelRequest,
    AgentRunInput,
    DefaultPlanningLayer,
    DefaultProfileSelector,
    ExecutionPlan,
    HumanApprovalRequest,
    LangGraphExecutor,
    ReviewDecision,
    TaskContext,
    WorkPlan,
    WorkPlanStep,
    parse_review_decision,
    parse_work_plan,
)
from packages.agent_harness.capabilities import CapabilitySnapshot
from packages.agent_harness.context import ContextBuilder
from packages.tools import (
    ToolApprovalRequiredError,
    ToolCatalog,
    ToolDescriptor,
    ToolInvocation,
    ToolRegistry,
    ToolSourceStatus,
    ToolSpec,
)


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v3-plan-review.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class ComplexAgentModel:
    def __init__(
        self,
        *,
        decisions: list[AgentDecision],
        reviews: list[ReviewDecision],
        plan: WorkPlan | None = None,
    ) -> None:
        self.decisions = decisions
        self.reviews = reviews
        self.plan = plan or WorkPlan(
            goal="核对并总结",
            steps=(
                WorkPlanStep(
                    objective="核对来源",
                    acceptance_criteria=("至少一个可核验结论",),
                ),
                WorkPlanStep(
                    objective="形成回答",
                    acceptance_criteria=("结论清晰",),
                ),
            ),
        )
        self.plan_requests: list[AgentModelRequest] = []
        self.decision_requests: list[AgentModelRequest] = []
        self.review_requests: list[AgentModelRequest] = []

    async def create_plan(self, request: AgentModelRequest) -> WorkPlan:
        self.plan_requests.append(request)
        return self.plan

    async def decide(self, request: AgentModelRequest) -> AgentDecision:
        self.decision_requests.append(request)
        if not self.decisions:
            raise AssertionError("Unexpected decision call")
        return self.decisions.pop(0)

    async def review(self, request: AgentModelRequest) -> ReviewDecision:
        self.review_requests.append(request)
        if not self.reviews:
            raise AssertionError("Unexpected review call")
        return self.reviews.pop(0)


def memory_saver() -> InMemorySaver:
    return InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=None),
    )


def complex_run_input(
    task: Task,
    *,
    require_plan_approval: bool = False,
    max_review_retries: int = 1,
    max_replans: int = 1,
) -> AgentRunInput:
    return AgentRunInput(
        plan=ExecutionPlan(
            goal="研究 checkpoint",
            steps=("核对资料", "总结结论"),
            allowed_tools=(),
            approval_required_tools=(),
            max_steps=12,
            timeout_seconds=30.0,
            risk_level="medium",
            output_format="markdown",
            profile_name="v2.researcher",
            executor_kind="langgraph",
            workflow_key="langgraph.learn",
            execution_mode="plan_execute_review",
            require_plan_approval=require_plan_approval,
            max_review_retries=max_review_retries,
            max_replans=max_replans,
        ),
        context=TaskContext(
            task_id=task.id,
            user_id=task.user_id,
            task_type="learn",
            input_text="/learn checkpoint",
            memory_summary="",
        ),
    )


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    status: str = "running",
) -> Task:
    async with sessionmaker() as session:
        user = User(display_name="V3-09 User")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type="learn",
            input_text="/learn checkpoint",
            status=status,
        )
        session.add(task)
        await session.commit()
        return task


def test_profiles_select_hybrid_modes_and_bounded_review_policy() -> None:
    selector = DefaultProfileSelector()
    planner = DefaultPlanningLayer()
    context_builder = ContextBuilder()
    capability = CapabilitySnapshot(
        revision=1,
        allowed_tools=(),
        approval_required_tools=(),
        summaries=(),
    )

    def build(task_type: str) -> ExecutionPlan:
        task = Task(
            id=f"task-{task_type}",
            user_id="user-1",
            platform="api",
            task_type=task_type,
            input_text=f"/{task_type} 测试",
            status="pending",
        )
        profile = selector.select(task)
        context = context_builder.build(
            task=task,
            user=User(id="user-1", display_name="User"),
            memory_summary="",
            skills=(),
            capabilities=capability,
        )
        return planner.build_plan(task=task, profile=profile, context=context)

    assert build("plan").execution_mode == "react"
    assert build("office").execution_mode == "react"
    learn = build("learn")
    daily = build("daily")
    assert learn.execution_mode == "plan_execute_review"
    assert daily.execution_mode == "plan_execute_review"
    assert (learn.max_review_retries, learn.max_replans) == (1, 1)
    assert learn.require_plan_approval is False


def test_structured_plan_and_review_parsers_fail_closed() -> None:
    plan = parse_work_plan(
        '{"goal":"核对事实","steps":['
        '{"objective":"查证","acceptance_criteria":["来源可核验"]}]}'
    )
    review = parse_review_decision('{"status":"retry","feedback":"补充来源"}')

    assert plan.steps[0].objective == "查证"
    assert review == ReviewDecision(status="retry", feedback="补充来源")
    with pytest.raises(ValueError):
        parse_work_plan('{"goal":"空计划","steps":[]}')
    with pytest.raises(ValueError):
        parse_review_decision('{"status":"loop_forever","feedback":"x"}')


@pytest.mark.asyncio
async def test_complex_graph_reviews_candidate_and_allows_one_retry(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    model = ComplexAgentModel(
        decisions=[
            AgentDecision(action="final", answer="缺少来源"),
            AgentDecision(action="final", answer="已补充可核验来源"),
        ],
        reviews=[
            ReviewDecision(status="retry", feedback="补充来源"),
            ReviewDecision(status="pass", feedback="满足要求"),
        ],
    )
    async with sessionmaker() as session:
        result = await LangGraphExecutor(
            session=session,
            tool_registry=ToolRegistry(session=session),
            model=model,
            checkpointer=memory_saver(),
        ).execute(run_input=complex_run_input(task))

    assert result.result_text == "已补充可核验来源"
    assert result.display_plan == ("核对来源", "形成回答")
    assert len(model.plan_requests) == 1
    assert len(model.decision_requests) == 2
    assert len(model.review_requests) == 2
    assert "补充来源" in "\n".join(
        message.content for message in model.decision_requests[1].messages
    )


@pytest.mark.asyncio
async def test_plan_approval_interrupt_persists_and_resumes_exact_gate(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    saver = memory_saver()
    model = ComplexAgentModel(
        decisions=[AgentDecision(action="final", answer="审批后的答案")],
        reviews=[ReviewDecision(status="pass", feedback="通过")],
    )
    run_input = complex_run_input(task, require_plan_approval=True)

    async with sessionmaker() as session:
        executor = LangGraphExecutor(
            session=session,
            tool_registry=ToolRegistry(session=session),
            model=model,
            checkpointer=saver,
        )
        waiting = await executor.execute(run_input=run_input)
        assert waiting.approval_requests == (
            HumanApprovalRequest(
                approval_type="plan",
                subject="plan:0",
                summary="核对来源；形成回答",
            ),
        )
        await TaskService(session).save_waiting_approval(
            task.id,
            waiting.result_text,
            approval_requests=waiting.approval_requests,
        )
        approval = await session.scalar(select(Approval))
        assert approval is not None
        approval.status = "approved"
        approval.decided_by_user_id = task.user_id
        task_row = await session.get(Task, task.id)
        assert task_row is not None
        task_row.status = "pending"
        await session.commit()

        completed = await executor.execute(run_input=run_input)

    assert completed.result_text == "审批后的答案"
    assert len(model.plan_requests) == 1


@pytest.mark.asyncio
async def test_review_escalation_requires_exact_review_approval(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    saver = memory_saver()
    model = ComplexAgentModel(
        decisions=[AgentDecision(action="final", answer="需要人确认的候选答案")],
        reviews=[ReviewDecision(status="escalate", feedback="结论存在歧义")],
    )
    run_input = complex_run_input(task)

    async with sessionmaker() as session:
        executor = LangGraphExecutor(
            session=session,
            tool_registry=ToolRegistry(session=session),
            model=model,
            checkpointer=saver,
        )
        waiting = await executor.execute(run_input=run_input)
        request = waiting.approval_requests[0]
        assert request.approval_type == "review"
        assert request.subject == "review:0:0"
        assert "结论存在歧义" in request.summary

        # 相同 task 上的 plan 批准不能满足 review gate。
        session.add(
            Approval(
                task_id=task.id,
                tool_name="agent.plan",
                approval_type=ApprovalType.PLAN.value,
                subject=request.subject,
                request_summary="错误类型",
                status="approved",
                decided_by_user_id=task.user_id,
            )
        )
        await session.commit()
        with pytest.raises(RuntimeError, match="exact human approval"):
            await executor.execute(run_input=run_input)


@pytest.mark.asyncio
async def test_non_tool_approval_never_authorizes_gated_tool(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    calls: list[str] = []
    descriptor = ToolDescriptor(
        name="email.send",
        description="发送邮件",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        source_id="builtin",
        source_kind="builtin",
        version="1",
        enabled=True,
        risk_level="L3",
        requires_approval=True,
    )
    snapshot = ToolCatalog.snapshot(
        revision=1,
        descriptors=(descriptor,),
        sources=(ToolSourceStatus("builtin", "builtin", available=True),),
    )

    async def send(invocation: ToolInvocation) -> dict[str, bool]:
        calls.append(invocation.name)
        return {"sent": True}

    async with sessionmaker() as session:
        session.add(
            Approval(
                task_id=task.id,
                tool_name="email.send",
                approval_type=ApprovalType.REVIEW.value,
                subject="email.send",
                request_summary="不是工具审批",
                status="approved",
                decided_by_user_id=task.user_id,
            )
        )
        registry = ToolRegistry(session=session, snapshot_revision=1)
        registry.register(
            ToolSpec(
                name="email.send",
                description="发送邮件",
                risk_level="L3",
                handler=send,
                version="1",
            )
        )
        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=task.user_id,
                    name="email.send",
                    tool_snapshot_revision=snapshot.revision,
                    tool_version="1",
                ),
                allowed_tools=(),
                approval_required_tools=("email.send",),
            )

    assert calls == []
