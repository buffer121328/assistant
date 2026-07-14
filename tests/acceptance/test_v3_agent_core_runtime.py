from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
import json
from pathlib import Path
from typing import Any

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

from assistant_api.agent_model import AgentGatewayModel
from assistant_api.checkpoints import (
    AgentCheckpointConfigurationError,
    build_checkpoint_serializer,
    normalize_checkpoint_database_url,
    open_agent_checkpointer,
)
from assistant_api.config import Settings
from assistant_api.models import Approval, Base, ModelLog, Task, ToolLog, User
from assistant_api.worker_runtime import execute_task_by_id
from packages.agent_harness import (
    AgentDecision,
    AgentModelRequest,
    AgentRunInput,
    ExecutionPlan,
    LangGraphExecutor,
    LoopStepLimitError,
    TaskContext,
)
from packages.agent_harness.agent_model import (
    AgentDecisionError,
    build_agent_model_request,
    parse_agent_decision,
)
from packages.model_gateway import GatewayResult, GatewayUsage
from packages.observability import NoopObservation
from packages.tools import (
    ToolCatalog,
    ToolDescriptor,
    ToolInvocation,
    ToolNotAllowedError,
    ToolRegistry,
    ToolSourceStatus,
    ToolSpec,
)


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v3-agent-core.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def run_input() -> AgentRunInput:
    return AgentRunInput(
        plan=ExecutionPlan(
            goal="研究 LangGraph checkpoint",
            steps=("确认问题", "必要时搜索", "给出结论"),
            allowed_tools=("search.web",),
            approval_required_tools=(),
            max_steps=6,
            timeout_seconds=30.0,
            risk_level="low",
            output_format="markdown",
            profile_name="v2.researcher",
            executor_kind="langgraph",
            workflow_key="langgraph.learn",
            tool_snapshot_revision=7,
            tool_versions=(("search.web", "1"),),
        ),
        context=TaskContext(
            task_id="task-agent-core",
            user_id="user-agent-core",
            task_type="learn",
            input_text="/learn LangGraph checkpoint",
            memory_summary="用户偏好先给结论",
            skill_names=("research",),
            skill_instructions=("引用来源并区分事实与推断",),
            allowed_tools=("search.web",),
            capability_summary=("search.web: 搜索公开资料",),
        ),
    )


class RecordingObservability:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.flush_count = 0
        self.shutdown_count = 0

    @contextmanager
    def observe(self, name: str, **kwargs: Any) -> Any:
        self.events.append((name, kwargs))
        yield NoopObservation()

    def score(self, **kwargs: Any) -> None:
        self.events.append(("score", kwargs))

    def flush(self) -> None:
        self.flush_count += 1

    def shutdown(self) -> None:
        self.shutdown_count += 1


class SequenceAgentModel:
    def __init__(
        self,
        decisions: list[AgentDecision],
        *,
        repeat_last: bool = False,
        delay_seconds: float = 0.0,
    ) -> None:
        self.decisions = decisions
        self.repeat_last = repeat_last
        self.delay_seconds = delay_seconds
        self.requests: list[AgentModelRequest] = []
        self._index = 0

    async def decide(self, request: AgentModelRequest) -> AgentDecision:
        self.requests.append(request)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self._index < len(self.decisions):
            decision = self.decisions[self._index]
            self._index += 1
            return decision
        if self.repeat_last and self.decisions:
            return self.decisions[-1]
        raise AssertionError("Unexpected Agent model call")


class FakeGatewayAdapter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[tuple[Any, str]] = []

    async def chat(self, request: Any, model_class: str) -> GatewayResult:
        self.calls.append((request, model_class))
        return GatewayResult(
            provider="deepseek",
            model="deepseek-standard-test",
            content=self.content,
            usage=GatewayUsage(input_tokens=12, output_tokens=6),
            latency_ms=9,
        )


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    status: str = "running",
    task_type: str = "learn",
) -> Task:
    async with sessionmaker() as session:
        user = User(display_name="V3 Agent Core User")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type=task_type,
            input_text=f"/{task_type} LangGraph checkpoint",
            status=status,
        )
        session.add(task)
        await session.commit()
        return task


def search_descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="search.web",
        description="搜索公开资料",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        source_id="builtin",
        source_kind="builtin",
        version="1",
        enabled=True,
        risk_level="L1",
        requires_approval=False,
        tags=("learn",),
    )


def approval_descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="email.send",
        description="发送邮件",
        input_schema={
            "type": "object",
            "properties": {"subject": {"type": "string"}},
            "required": ["subject"],
            "additionalProperties": False,
        },
        source_id="builtin",
        source_kind="builtin",
        version="1",
        enabled=True,
        risk_level="L3",
        requires_approval=True,
        tags=("office",),
    )


def tool_snapshot(
    descriptors: tuple[ToolDescriptor, ...] | None = None,
) -> Any:
    return ToolCatalog.snapshot(
        revision=7,
        descriptors=descriptors or (search_descriptor(),),
        sources=(ToolSourceStatus("builtin", "builtin", available=True),),
    )


def task_run_input(
    task: Task,
    *,
    max_steps: int = 6,
    timeout_seconds: float = 30.0,
) -> AgentRunInput:
    base = run_input()
    return AgentRunInput(
        plan=ExecutionPlan(
            **{
                **base.plan.__dict__,
                "max_steps": max_steps,
                "timeout_seconds": timeout_seconds,
            }
        ),
        context=TaskContext(
            **{
                **base.context.__dict__,
                "task_id": task.id,
                "user_id": task.user_id,
            }
        ),
    )


def approval_run_input(task: Task) -> AgentRunInput:
    base = task_run_input(task)
    return AgentRunInput(
        plan=ExecutionPlan(
            **{
                **base.plan.__dict__,
                "allowed_tools": (),
                "approval_required_tools": ("email.send",),
                "tool_versions": (("email.send", "1"),),
            }
        ),
        context=TaskContext(
            **{
                **base.context.__dict__,
                "allowed_tools": (),
                "approval_required_tools": ("email.send",),
            }
        ),
    )


def memory_saver() -> InMemorySaver:
    return InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=None),
    )


def test_agent_decision_accepts_bounded_final_and_tool_call() -> None:
    final = parse_agent_decision(
        json.dumps(
            {
                "action": "final",
                "answer": "Checkpoint 会在图步骤后保存状态。",
                "plan": ["解释概念", "说明恢复边界"],
            },
            ensure_ascii=False,
        )
    )
    tool = parse_agent_decision(
        json.dumps(
            {
                "action": "tool_call",
                "tool_name": "search.web",
                "arguments": {"query": "LangGraph checkpoint persistence"},
                "plan": ["查询官方资料", "整理结论"],
            },
            ensure_ascii=False,
        )
    )

    assert final.action == "final"
    assert final.answer == "Checkpoint 会在图步骤后保存状态。"
    assert final.plan == ("解释概念", "说明恢复边界")
    assert final.tool_name is None
    assert tool.action == "tool_call"
    assert tool.tool_name == "search.web"
    assert tool.arguments == {"query": "LangGraph checkpoint persistence"}
    assert tool.answer is None


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps({"action": "final", "answer": ""}),
        json.dumps({"action": "unknown", "answer": "x"}),
        json.dumps(
            {
                "action": "tool_call",
                "tool_name": "search.web",
                "arguments": [],
            }
        ),
        json.dumps(
            {
                "action": "tool_call",
                "tool_name": "search.web",
                "arguments": {},
                "answer": "同时回答",
            }
        ),
        json.dumps(
            {
                "action": "final",
                "answer": "x",
                "plan": ["1", "2", "3", "4", "5", "6"],
            }
        ),
    ],
)
def test_agent_decision_rejects_invalid_or_ambiguous_payload(payload: str) -> None:
    with pytest.raises(AgentDecisionError):
        parse_agent_decision(payload)


def test_agent_model_request_contains_only_bounded_context() -> None:
    secret = "agent-core-secret"
    request = build_agent_model_request(
        run_input(),
        tool_schemas=(
            {
                "type": "function",
                "function": {
                    "name": "search.web",
                    "description": "搜索公开资料",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
        ),
        history=(
            {
                "role": "tool",
                "name": "search.web",
                "content": f"已找到资料；内部值 {secret}",
            },
        ),
        sensitive_values=(secret,),
    )

    combined = "\n".join(message.content for message in request.messages)
    assert request.task_id == "task-agent-core"
    assert request.task_type == "learn"
    assert "v2.researcher" in combined
    assert "用户偏好先给结论" in combined
    assert "引用来源并区分事实与推断" in combined
    assert "search.web" in combined
    assert '"query"' in combined
    assert "shell.exec" not in combined
    assert secret not in combined
    assert "[REDACTED]" in combined
    assert "不要输出隐式思维链" in combined


def test_model_generated_plan_cannot_change_execution_envelope() -> None:
    original = run_input().plan
    decision = parse_agent_decision(
        json.dumps(
            {
                "action": "final",
                "answer": "完成",
                "plan": ["调用 shell.exec", "无限循环"],
            },
            ensure_ascii=False,
        )
    )

    assert decision.plan == ("调用 shell.exec", "无限循环")
    assert original.allowed_tools == ("search.web",)
    assert original.max_steps == 6
    assert original.timeout_seconds == 30.0


@pytest.mark.asyncio
async def test_agent_loop_uses_model_selected_tool_then_returns_model_answer(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    calls: list[ToolInvocation] = []

    async def search(invocation: ToolInvocation) -> dict[str, Any]:
        calls.append(invocation)
        return {
            "results": [
                {
                    "title": "LangGraph persistence",
                    "url": "https://example.invalid/langgraph",
                }
            ]
        }

    model = SequenceAgentModel(
        [
            AgentDecision(
                action="tool_call",
                tool_name="search.web",
                arguments={"query": "LangGraph checkpoint persistence"},
                plan=("查询资料", "整理结论"),
            ),
            AgentDecision(
                action="final",
                answer="LangGraph 会按 thread 保存图状态。",
                plan=("查询资料", "解释 checkpoint"),
            ),
        ]
    )
    observability = RecordingObservability()
    async with sessionmaker() as session:
        registry = ToolRegistry(session=session, snapshot_revision=7)
        registry.register(
            ToolSpec(
                name="search.web",
                description="搜索公开资料",
                risk_level="L1",
                handler=search,
                input_schema=dict(search_descriptor().input_schema),
                version="1",
            )
        )
        result = await LangGraphExecutor(
            session=session,
            tool_registry=registry,
            model=model,
            checkpointer=memory_saver(),
            tool_snapshot=tool_snapshot(),
            observability=observability,
        ).execute(run_input=task_run_input(task))
        await session.commit()

    second_request = "\n".join(
        message.content for message in model.requests[1].messages
    )
    assert result.result_text == "LangGraph 会按 thread 保存图状态。"
    assert result.display_plan == ("查询资料", "解释 checkpoint")
    assert result.tool_calls == ("search.web",)
    assert result.checkpoint_id
    assert len(calls) == 1
    assert calls[0].arguments == {"query": "LangGraph checkpoint persistence"}
    assert "LangGraph persistence" in second_request
    observation_names = [name for name, _payload in observability.events]
    assert observation_names == [
        "agent.graph.prepare",
        "agent.graph.model",
        "agent.graph.tool",
        "agent.tool.call",
        "agent.graph.model",
    ]


@pytest.mark.asyncio
async def test_agent_loop_rejects_unplanned_tool_before_handler(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    model = SequenceAgentModel(
        [
            AgentDecision(
                action="tool_call",
                tool_name="shell.exec",
                arguments={"command": "whoami"},
            )
        ]
    )
    async with sessionmaker() as session:
        registry = ToolRegistry(session=session, snapshot_revision=7)
        registry.register(
            ToolSpec(
                name="search.web",
                description="搜索公开资料",
                risk_level="L1",
                handler=lambda invocation: None,  # type: ignore[arg-type]
                version="1",
            )
        )
        executor = LangGraphExecutor(
            session=session,
            tool_registry=registry,
            model=model,
            checkpointer=memory_saver(),
            tool_snapshot=tool_snapshot(),
        )
        with pytest.raises(ToolNotAllowedError):
            await executor.execute(run_input=task_run_input(task))
        await session.commit()
        logs = list(
            await session.scalars(
                select(ToolLog).where(ToolLog.task_id == task.id)
            )
        )

    assert any(
        log.tool_name == "shell.exec" and log.status == "failed" for log in logs
    )


@pytest.mark.asyncio
async def test_agent_loop_enforces_step_and_timeout_limits(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    repeating = SequenceAgentModel(
        [
            AgentDecision(
                action="tool_call",
                tool_name="search.web",
                arguments={"query": "repeat"},
            )
        ],
        repeat_last=True,
    )

    async def search(invocation: ToolInvocation) -> dict[str, bool]:
        del invocation
        return {"ok": True}

    async with sessionmaker() as session:
        registry = ToolRegistry(session=session, snapshot_revision=7)
        registry.register(
            ToolSpec(
                name="search.web",
                description="搜索公开资料",
                risk_level="L1",
                handler=search,
                version="1",
            )
        )
        executor = LangGraphExecutor(
            session=session,
            tool_registry=registry,
            model=repeating,
            checkpointer=memory_saver(),
            tool_snapshot=tool_snapshot(),
        )
        with pytest.raises(LoopStepLimitError):
            await executor.execute(run_input=task_run_input(task, max_steps=3))

        slow = LangGraphExecutor(
            session=session,
            tool_registry=registry,
            model=SequenceAgentModel(
                [AgentDecision(action="final", answer="too late")],
                delay_seconds=0.05,
            ),
            checkpointer=memory_saver(),
            tool_snapshot=tool_snapshot(),
        )
        with pytest.raises(TimeoutError):
            await slow.execute(
                run_input=task_run_input(task, timeout_seconds=0.01)
            )


@pytest.mark.asyncio
async def test_approval_interrupt_resumes_same_task_and_revalidates_registry(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    saver = memory_saver()
    calls: list[ToolInvocation] = []

    async def send_email(invocation: ToolInvocation) -> dict[str, bool]:
        calls.append(invocation)
        return {"sent": True}

    first_model = SequenceAgentModel(
        [
            AgentDecision(
                action="tool_call",
                tool_name="email.send",
                arguments={"subject": "周报"},
                plan=("请求发送审批", "发送后确认"),
            )
        ]
    )
    descriptor = approval_descriptor()
    snapshot = tool_snapshot((descriptor,))
    async with sessionmaker() as session:
        registry = ToolRegistry(session=session, snapshot_revision=7)
        registry.register(
            ToolSpec(
                name="email.send",
                description="发送邮件",
                risk_level="L3",
                handler=send_email,
                input_schema=dict(descriptor.input_schema),
                version="1",
            )
        )
        waiting = await LangGraphExecutor(
            session=session,
            tool_registry=registry,
            model=first_model,
            checkpointer=saver,
            tool_snapshot=snapshot,
        ).execute(run_input=approval_run_input(task))
        await session.commit()

        assert waiting.requested_tools == ("email.send",)
        assert waiting.checkpoint_id
        assert calls == []

        session.add(
            Approval(
                task_id=task.id,
                tool_name="email.send",
                status="approved",
                decided_by_user_id=task.user_id,
            )
        )
        await session.commit()

        final_model = SequenceAgentModel(
            [
                AgentDecision(
                    action="final",
                    answer="邮件已发送。",
                    plan=("请求发送审批", "发送后确认"),
                )
            ]
        )
        completed = await LangGraphExecutor(
            session=session,
            tool_registry=registry,
            model=final_model,
            checkpointer=saver,
            tool_snapshot=snapshot,
        ).execute(run_input=approval_run_input(task))
        await session.commit()

    assert completed.result_text == "邮件已发送。"
    assert completed.requested_tools == ()
    assert completed.checkpoint_id
    assert completed.checkpoint_id != waiting.checkpoint_id
    assert len(first_model.requests) == 1
    assert len(final_model.requests) == 1
    assert len(calls) == 1
    assert calls[0].task_id == task.id
    assert calls[0].name == "email.send"


@pytest.mark.asyncio
async def test_agent_gateway_model_uses_existing_route_and_writes_model_log(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    adapter = FakeGatewayAdapter(
        json.dumps(
            {
                "action": "final",
                "answer": "网关生成的回答",
                "plan": ["读取上下文", "回答"],
            },
            ensure_ascii=False,
        )
    )
    request = build_agent_model_request(
        task_run_input(task),
        tool_schemas=(),
    )
    observability = RecordingObservability()

    async with sessionmaker() as session:
        decision = await AgentGatewayModel(
            session=session,
            settings=Settings(),
            adapter=adapter,
            observability=observability,
        ).decide(request)
        await session.commit()
        logs = list(
            await session.scalars(
                select(ModelLog).where(ModelLog.task_id == task.id)
            )
        )

    assert decision.answer == "网关生成的回答"
    assert len(adapter.calls) == 1
    gateway_request, model_class = adapter.calls[0]
    assert gateway_request.task_type == "learn"
    assert model_class == "standard"
    assert len(logs) == 1
    assert logs[0].error_message is None
    assert "deepseek-standard-test" in (logs[0].response_text or "")
    assert observability.events[0][0] == "agent.model.decision"
    assert observability.events[0][1]["as_type"] == "generation"


@pytest.mark.asyncio
async def test_agent_gateway_model_maps_office_route_and_audits_invalid_decision(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    adapter = FakeGatewayAdapter("invalid-agent-json")
    base = task_run_input(task)
    office_input = AgentRunInput(
        plan=base.plan,
        context=TaskContext(
            **{
                **base.context.__dict__,
                "task_type": "office",
            }
        ),
    )

    async with sessionmaker() as session:
        model = AgentGatewayModel(
            session=session,
            settings=Settings(),
            adapter=adapter,
        )
        with pytest.raises(AgentDecisionError):
            await model.decide(
                build_agent_model_request(office_input, tool_schemas=())
            )
        await session.commit()
        log = await session.scalar(
            select(ModelLog).where(ModelLog.task_id == task.id)
        )

    assert adapter.calls[0][0].task_type == "office_text"
    assert adapter.calls[0][1] == "standard"
    assert log is not None
    assert log.response_text is None
    assert "Agent decision" in (log.error_message or "")


@pytest.mark.asyncio
async def test_postgres_checkpointer_lifecycle_is_strict_and_injectable() -> None:
    calls: dict[str, Any] = {}

    class FakePostgresSaver:
        async def setup(self) -> None:
            calls["setup"] = calls.get("setup", 0) + 1

    saver = FakePostgresSaver()

    @asynccontextmanager
    async def saver_factory(
        connection_string: str,
        *,
        serde: Any,
    ) -> AsyncIterator[FakePostgresSaver]:
        calls["connection_string"] = connection_string
        calls["serde"] = serde
        try:
            yield saver
        finally:
            calls["closed"] = True

    database_url = (
        "postgresql+asyncpg://assistant:placeholder@postgres:5432/assistant"
    )
    async with open_agent_checkpointer(
        database_url,
        saver_factory=saver_factory,
    ) as opened:
        assert opened is saver
        assert calls.get("closed") is None

    assert calls["connection_string"] == (
        "postgresql://assistant:placeholder@postgres:5432/assistant"
    )
    assert calls["setup"] == 1
    assert calls["closed"] is True
    assert calls["serde"]._allowed_msgpack_modules is None


def test_checkpoint_configuration_rejects_non_postgres_without_fallback() -> None:
    serializer = build_checkpoint_serializer()
    assert serializer._allowed_msgpack_modules is None
    assert normalize_checkpoint_database_url(
        "postgresql://user:placeholder@db:5432/app"
    ) == "postgresql://user:placeholder@db:5432/app"

    with pytest.raises(AgentCheckpointConfigurationError):
        normalize_checkpoint_database_url("sqlite+aiosqlite:///local.db")


@pytest.mark.asyncio
async def test_worker_injected_executor_skips_model_and_checkpoint(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, status="pending", task_type="plan")

    class RecordingExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, *, run_input: AgentRunInput) -> Any:
            self.calls += 1
            return type(
                "Result",
                (),
                {
                    "result_text": "注入执行器完成",
                    "display_plan": (),
                    "tool_calls": (),
                    "requested_tools": (),
                    "loop_steps": 1,
                    "checkpoint_id": None,
                },
            )()

    executor = RecordingExecutor()
    observability = RecordingObservability()
    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=Settings(database_url="sqlite+aiosqlite:///must-not-open.db"),
        langgraph_executor=executor,
        observability=observability,
    )

    assert result.status == "success"
    assert result.result_text == "注入执行器完成"
    assert executor.calls == 1
    assert observability.events[0][0] == "agent.task"
    assert observability.flush_count == 1
    assert observability.shutdown_count == 0


@pytest.mark.asyncio
async def test_worker_runs_real_agent_core_with_injected_model_and_saver(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, status="pending", task_type="plan")
    model = SequenceAgentModel(
        [
            AgentDecision(
                action="final",
                answer="Agent Core worker result",
                plan=("理解目标", "回答"),
            )
        ]
    )

    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=Settings(database_url="sqlite+aiosqlite:///unused.db"),
        agent_model=model,
        checkpointer=memory_saver(),
    )

    assert result.status == "success"
    assert result.result_text == "Agent Core worker result"
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_worker_does_not_fake_persistence_for_non_postgres(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker, status="pending", task_type="plan")

    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=Settings(database_url="sqlite+aiosqlite:///no-fallback.db"),
    )

    assert result.status == "failed"
    assert result.error_message is not None
    assert "PostgreSQL" in result.error_message
