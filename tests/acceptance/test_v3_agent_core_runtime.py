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

from model_gateway.agent_model import AgentGatewayModel
from infrastructure.persistence.checkpoints import (
    AgentCheckpointConfigurationError,
    build_checkpoint_serializer,
    normalize_checkpoint_database_url,
    open_agent_checkpointer,
)
from infrastructure.settings.config import Settings
from domain.models import (
    AgentRun,
    Approval,
    Base,
    Conversation,
    ConversationMessage,
    ModelLog,
    Task,
    TaskEvent,
    ToolLog,
    User,
)
from workers.runtime import execute_task_by_id
from agent.governance.content import LocalContentGuard
from agent import (
    AgentDecision,
    AgentModelRequest,
    AgentToolCall,
    AgentRunInput,
    ExecutionPlan,
    LangGraphExecutor,
    LoopStepLimitError,
    ReviewDecision,
    TaskContext,
    WorkPlan,
)
from agent.prompting.model_contract import (
    AgentDecisionError,
    build_agent_model_request,
    parse_agent_decision,
)
from agent.prompting.types import PromptBuildResult
from model_gateway import GatewayResult, GatewayUsage
from infrastructure.telemetry.observability import NoopObservation
from tools import (
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


class ManagedPromptBuilderStub:
    def build(self, _runtime_context: dict[str, object] | None = None) -> PromptBuildResult:
        return PromptBuildResult(
            system_prompt="managed instructions",
            modules=(),
            fingerprint="prompt-fingerprint",
            metadata={},
        )


def test_managed_prompt_path_always_includes_agent_decision_contract() -> None:
    request = build_agent_model_request(
        run_input(),
        tool_schemas=(),
        prompt_builder=ManagedPromptBuilderStub(),
    )

    system_message = request.messages[0].content
    assert "managed instructions" in system_message
    assert '"action":"final"' in system_message
    assert '"action":"tool_call"' in system_message
    assert '"action":"tool_batch"' in system_message



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

    async def create_plan(self, request: AgentModelRequest) -> WorkPlan:
        raise AssertionError("ReAct test must not call plan model")

    async def review(self, request: AgentModelRequest) -> ReviewDecision:
        raise AssertionError("ReAct test must not call review model")


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


class SequenceGatewayAdapter:
    """Return deterministic provider results for structured-recovery tests."""

    def __init__(self, results: list[GatewayResult]) -> None:
        self.results = results
        self.calls: list[tuple[Any, str]] = []

    async def chat(self, request: Any, model_class: str) -> GatewayResult:
        """Return the next configured result and record the governed request."""
        self.calls.append((request, model_class))
        if not self.results:
            raise AssertionError("Unexpected gateway retry")
        return self.results.pop(0)


class StreamingFakeGatewayAdapter(FakeGatewayAdapter):
    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.stream_calls = 0

    async def chat_stream(
        self, request: Any, model_class: str, on_delta: Any
    ) -> GatewayResult:
        self.stream_calls += 1
        await on_delta('{"action":"final","answer":"unvalidated"}')
        return await self.chat(request, model_class)


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    status: str = "running",
    task_type: str = "learn",
    with_conversation: bool = False,
) -> Task:
    async with sessionmaker() as session:
        user = User(display_name="V3 Agent Core User")
        session.add(user)
        await session.flush()
        conversation_id = None
        if with_conversation:
            conversation = Conversation(
                user_id=user.id,
                title="Agent runtime",
                channel="local",
                external_key=None,
            )
            session.add(conversation)
            await session.flush()
            conversation_id = conversation.id
        task = Task(
            user_id=user.id,
            platform="api",
            task_type=task_type,
            input_text=f"/{task_type} LangGraph checkpoint",
            status=status,
            conversation_id=conversation_id,
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
            await session.scalars(select(ToolLog).where(ToolLog.task_id == task.id))
        )

    assert any(log.tool_name == "shell.exec" and log.status == "failed" for log in logs)


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
            await slow.execute(run_input=task_run_input(task, timeout_seconds=0.01))


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
        assert len(waiting.approval_requests) == 1
        assert waiting.approval_requests[0].subject.startswith("email.send:")
        assert waiting.checkpoint_id
        assert calls == []

        session.add(
            Approval(
                task_id=task.id,
                tool_name="email.send",
                subject=waiting.approval_requests[0].subject,
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
async def test_content_governance_disables_react_answer_streaming_in_langgraph_requests(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    model = SequenceAgentModel(
        [
            AgentDecision(
                action="final",
                answer="经过治理检查后才可返回的结论。",
            )
        ]
    )
    governance_events: list[tuple[str, dict[str, object]]] = []

    async def capture_event(event_type: str, payload: dict[str, object]) -> None:
        governance_events.append((event_type, dict(payload)))

    async with sessionmaker() as session:
        executor = LangGraphExecutor(
            session=session,
            tool_registry=ToolRegistry(session=session),
            model=model,
            checkpointer=memory_saver(),
            tool_snapshot=tool_snapshot(),
            content_guard=LocalContentGuard(),
            event_sink=capture_event,
        )
        result = await executor.execute(run_input=task_run_input(task))

    assert result.result_text == "经过治理检查后才可返回的结论。"
    assert len(model.requests) == 1
    assert model.requests[0].stream_answer is False
    assert model.requests[0].metadata["protect_final_answer_logs"] is True
    assert governance_events[0][0] == "governance.content"
    assert governance_events[0][1]["scope"] == "final_result"
    assert governance_events[0][1]["outcome"] == "allow"


@pytest.mark.asyncio
async def test_gateway_hides_unvalidated_final_answer_and_emits_no_delta_when_governed(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    raw_answer = "这个未验证答案不能写入模型审计正文。"
    adapter = StreamingFakeGatewayAdapter(
        json.dumps(
            {
                "action": "final",
                "answer": raw_answer,
            },
            ensure_ascii=False,
        )
    )
    published_events: list[tuple[str, dict[str, object]]] = []

    async def capture_event(event_type: str, payload: dict[str, object]) -> None:
        published_events.append((event_type, dict(payload)))

    request = build_agent_model_request(
        task_run_input(task),
        tool_schemas=(),
        stream_answer=False,
        protect_final_answer_logs=True,
    )

    async with sessionmaker() as session:
        model = AgentGatewayModel(
            session=session,
            settings=Settings(),
            adapter=adapter,
            event_sink=capture_event,
        )
        decision = await model.decide(request)
        await session.commit()
        log = await session.scalar(select(ModelLog).where(ModelLog.task_id == task.id))

    assert decision.answer == raw_answer
    assert adapter.stream_calls == 0
    assert [event_type for event_type, _payload in published_events] == [
        "task.phase.started",
        "task.phase.completed",
    ]
    assert not any(
        event_type == "task.message.delta" for event_type, _payload in published_events
    )
    assert log is not None
    assert raw_answer not in (log.response_text or "")
    assert "[CONTENT_WITHHELD_BY_GOVERNANCE]" in (log.response_text or "")


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
        run = AgentRun(
            task_id=task.id,
            user_id=task.user_id,
            attempt_no=1,
            status="running",
        )
        session.add(run)
        await session.flush()
        decision = await AgentGatewayModel(
            session=session,
            settings=Settings(),
            adapter=adapter,
            agent_run_id=run.id,
            observability=observability,
        ).decide(request)
        await session.commit()
        logs = list(
            await session.scalars(select(ModelLog).where(ModelLog.task_id == task.id))
        )

    assert decision.answer == "网关生成的回答"
    assert len(adapter.calls) == 1
    gateway_request, model_class = adapter.calls[0]
    assert gateway_request.task_type == "learn"
    assert model_class == "standard"
    assert len(logs) == 1
    assert logs[0].agent_run_id == run.id
    assert logs[0].error_message is None
    assert "deepseek-standard-test" in (logs[0].response_text or "")
    assert observability.events[0][0] == "agent.model.decision"
    assert observability.events[0][1]["as_type"] == "generation"


@pytest.mark.asyncio
async def test_gateway_model_recovers_one_invalid_structured_decision(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A malformed first response is audited and regenerated once without tool use."""
    task = await create_task(sessionmaker, status="pending", task_type="learn")
    adapter = SequenceGatewayAdapter(
        [
            GatewayResult(
                provider="deepseek",
                model="deepseek-standard-test",
                content='{"action":"final","answer":"被截断',
                usage=GatewayUsage(input_tokens=10, output_tokens=1024),
                latency_ms=8,
                diagnostics={"finish_reason": "length"},
            ),
            GatewayResult(
                provider="deepseek",
                model="deepseek-standard-test",
                content='{"action":"final","answer":"恢复后的回答","plan":[]}',
                usage=GatewayUsage(input_tokens=12, output_tokens=8),
                latency_ms=6,
                diagnostics={"finish_reason": "stop"},
            ),
        ]
    )
    published: list[tuple[str, dict[str, object]]] = []

    async def capture_event(event_type: str, payload: dict[str, object]) -> None:
        """Capture bounded phase facts for assertions."""
        published.append((event_type, dict(payload)))

    request = build_agent_model_request(
        task_run_input(task),
        tool_schemas=(),
        protect_final_answer_logs=True,
    )
    async with sessionmaker() as session:
        model = AgentGatewayModel(
            session=session,
            settings=Settings(),
            adapter=adapter,
            event_sink=capture_event,
        )
        decision = await model.decide(request)
        await session.commit()
        logs = list(
            await session.scalars(
                select(ModelLog)
                .where(ModelLog.task_id == task.id)
                .order_by(ModelLog.created_at)
            )
        )

    assert decision.answer == "恢复后的回答"
    assert len(adapter.calls) == 2
    assert adapter.calls[0][0].max_tokens == 4096
    assert len(adapter.calls[1][0].messages) == len(adapter.calls[0][0].messages) + 1
    assert len(logs) == 2
    assert logs[0].error_message is not None
    assert "finish_reason" in (logs[0].response_text or "")
    assert "被截断" not in (logs[0].response_text or "")
    assert "INVALID_CONTENT_WITHHELD_BY_GOVERNANCE" in (logs[0].response_text or "")
    assert logs[1].error_message is None
    assert [event_type for event_type, _payload in published] == [
        "task.phase.started",
        "task.phase.retrying",
        "task.phase.completed",
    ]
    assert published[1][1] == {
        "phase": "answer_generation",
        "attempt": 2,
        "reason_code": "truncated_structured_output",
        "retryable": True,
        "finish_reason": "length",
    }


@pytest.mark.asyncio
async def test_gateway_model_exhausts_structured_recovery_with_safe_phase_failure(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Two invalid responses fail closed and publish only stable diagnostics."""
    task = await create_task(sessionmaker, status="pending", task_type="learn")
    adapter = SequenceGatewayAdapter(
        [
            GatewayResult(
                "deepseek",
                "deepseek-standard-test",
                "not-json-one",
                GatewayUsage(2, 2),
                1,
            ),
            GatewayResult(
                "deepseek",
                "deepseek-standard-test",
                "not-json-two",
                GatewayUsage(2, 2),
                1,
            ),
        ]
    )
    published: list[tuple[str, dict[str, object]]] = []

    async def capture_event(event_type: str, payload: dict[str, object]) -> None:
        """Capture bounded phase facts for assertions."""
        published.append((event_type, dict(payload)))

    request = build_agent_model_request(task_run_input(task), tool_schemas=())
    async with sessionmaker() as session:
        model = AgentGatewayModel(
            session=session,
            settings=Settings(),
            adapter=adapter,
            event_sink=capture_event,
        )
        with pytest.raises(AgentDecisionError, match="valid JSON"):
            await model.decide(request)
        await session.commit()

    assert len(adapter.calls) == 2
    assert published[-1] == (
        "task.phase.failed",
        {
            "phase": "answer_generation",
            "attempt": 2,
            "reason_code": "structured_json_extraction_failed",
            "retryable": True,
        },
    )


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
            await model.decide(build_agent_model_request(office_input, tool_schemas=()))
        await session.commit()
        log = await session.scalar(select(ModelLog).where(ModelLog.task_id == task.id))

    assert adapter.calls[0][0].task_type == "office_text"
    assert adapter.calls[0][1] == "standard"
    assert log is not None
    assert len(adapter.calls) == 2
    assert log.response_text is not None
    assert "invalid-agent-json" in log.response_text
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

    database_url = "postgresql+asyncpg://assistant:placeholder@postgres:5432/assistant"
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
    assert (
        normalize_checkpoint_database_url("postgresql://user:placeholder@db:5432/app")
        == "postgresql://user:placeholder@db:5432/app"
    )

    with pytest.raises(AgentCheckpointConfigurationError):
        normalize_checkpoint_database_url("sqlite+aiosqlite:///local.db")


@pytest.mark.asyncio
async def test_worker_passes_current_agent_run_id_to_runtime_dependencies(
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = await create_task(sessionmaker, status="pending", task_type="plan")
    captured: list[str | None] = []

    async def fake_execute_with_runtime_dependencies(**kwargs: Any) -> Task:
        captured.append(kwargs["agent_run_id"])
        session = kwargs["session"]
        stored = await session.get(Task, kwargs["task_id"])
        assert stored is not None
        stored.status = "success"
        stored.result_text = "captured run"
        await session.commit()
        return stored

    monkeypatch.setattr(
        "workers.runtime._execute_with_runtime_dependencies",
        fake_execute_with_runtime_dependencies,
    )

    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=Settings(database_url="sqlite+aiosqlite:///unused.db"),
        observability=RecordingObservability(),
    )

    async with sessionmaker() as session:
        run = await session.scalar(select(AgentRun).where(AgentRun.task_id == task.id))

    assert result.status == "success"
    assert run is not None
    assert captured == [run.id]


@pytest.mark.asyncio
async def test_worker_injected_executor_skips_model_and_checkpoint(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(
        sessionmaker,
        status="pending",
        task_type="plan",
        with_conversation=True,
    )

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

    async with sessionmaker() as session:
        assistant_messages = list(
            await session.scalars(
                select(ConversationMessage).where(
                    ConversationMessage.task_id == task.id,
                    ConversationMessage.role == "assistant",
                )
            )
        )
        event_types = list(
            await session.scalars(
                select(TaskEvent.event_type).where(TaskEvent.task_id == task.id)
            )
        )
    assert [message.content for message in assistant_messages] == ["注入执行器完成"]
    assert "task.message.completed" in event_types
    assert "task.completed" in event_types
    assert "task.status.changed" in event_types


@pytest.mark.asyncio
async def test_worker_publishes_progress_before_executor_returns(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Progress events are independently visible while the main task is still running."""
    task = await create_task(
        sessionmaker,
        status="pending",
        task_type="plan",
        with_conversation=True,
    )

    class InspectingExecutor:
        async def execute(self, *, run_input: AgentRunInput) -> Any:
            """Assert another session can read progress before terminal completion."""
            async with sessionmaker() as inspection_session:
                rows = list(
                    await inspection_session.scalars(
                        select(TaskEvent)
                        .where(TaskEvent.task_id == run_input.context.task_id)
                        .order_by(TaskEvent.sequence)
                    )
                )
            assert [row.event_type for row in rows][:3] == [
                "task.started",
                "task.plan.created",
                "plan",
            ]
            assert any(row.event_type == "task.action.started" for row in rows)
            return type(
                "Result",
                (),
                {
                    "result_text": "实时进度可见",
                    "display_plan": (),
                    "tool_calls": (),
                    "requested_tools": (),
                    "loop_steps": 1,
                    "checkpoint_id": None,
                },
            )()

    result = await execute_task_by_id(
        task.id,
        sessionmaker=sessionmaker,
        settings=Settings(database_url="sqlite+aiosqlite:///unused.db"),
        langgraph_executor=InspectingExecutor(),
        observability=RecordingObservability(),
    )

    assert result.status == "success"


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
    async with sessionmaker() as session:
        agent_run = await session.scalar(
            select(AgentRun).where(AgentRun.task_id == task.id)
        )
    assert agent_run is not None
    assert agent_run.status == "success"
    assert agent_run.attempt_no == 1
    assert agent_run.graph_version == "langgraph-v2"
    assert agent_run.ended_at is not None


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
    async with sessionmaker() as session:
        agent_run = await session.scalar(
            select(AgentRun).where(AgentRun.task_id == task.id)
        )
    assert agent_run is not None
    assert agent_run.status == "failed"
    assert agent_run.error_message is not None
    assert "PostgreSQL" in agent_run.error_message


@pytest.mark.asyncio
async def test_tool_batch_records_each_tool_call_observation(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    task = await create_task(sessionmaker)
    calls: list[str] = []

    async def handler(invocation: ToolInvocation) -> dict[str, Any]:
        calls.append(invocation.name)
        return {"name": invocation.name}

    model = SequenceAgentModel(
        [
            AgentDecision(
                action="tool_batch",
                tool_calls=(
                    AgentToolCall("call-a", "search.web", {"query": "a"}),
                    AgentToolCall("call-b", "knowledge.lookup", {"query": "b"}),
                ),
                plan=("并行查询",),
            ),
            AgentDecision(action="final", answer="完成", plan=("总结",)),
        ]
    )
    observability = RecordingObservability()
    async with sessionmaker() as session:
        registry = ToolRegistry(session=session, snapshot_revision=7)
        for name in ("search.web", "knowledge.lookup"):
            registry.register(
                ToolSpec(
                    name=name,
                    description=name,
                    risk_level="L1",
                    handler=handler,
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    version="1",
                    parallel_safe=True,
                )
            )
        base_input = task_run_input(task)
        batch_input = AgentRunInput(
            plan=ExecutionPlan(
                **{
                    **base_input.plan.__dict__,
                    "allowed_tools": ("search.web", "knowledge.lookup"),
                    "tool_versions": (
                        ("search.web", "1"),
                        ("knowledge.lookup", "1"),
                    ),
                }
            ),
            context=TaskContext(
                **{
                    **base_input.context.__dict__,
                    "allowed_tools": ("search.web", "knowledge.lookup"),
                }
            ),
        )
        result = await LangGraphExecutor(
            session=session,
            tool_registry=registry,
            model=model,
            checkpointer=memory_saver(),
            tool_snapshot=tool_snapshot(
                (
                    search_descriptor(),
                    ToolDescriptor(
                        name="knowledge.lookup",
                        description="查询知识库",
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
                        parallel_safe=True,
                    ),
                )
            ),
            observability=observability,
            agent_run_id="agent-run-1",
        ).execute(run_input=batch_input)
        await session.commit()

    assert result.result_text == "完成"
    assert calls == ["search.web", "knowledge.lookup"]
    tool_events = [
        payload for name, payload in observability.events if name == "agent.tool.call"
    ]
    assert len(tool_events) == 2
    assert {payload["metadata"]["tool_name"] for payload in tool_events} == {
        "search.web",
        "knowledge.lookup",
    }
    assert all(payload["metadata"]["batch"] is True for payload in tool_events)
    assert all(
        payload["metadata"]["agent_run_id"] == "agent-run-1" for payload in tool_events
    )

@pytest.mark.asyncio
async def test_gateway_model_classifies_invalid_decision_contract_separately(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A readable JSON object with an invalid action gets a contract diagnostic."""
    task = await create_task(sessionmaker, status="pending", task_type="learn")
    adapter = SequenceGatewayAdapter(
        [
            GatewayResult(
                "deepseek",
                "deepseek-standard-test",
                '{"action":"unknown","answer":"回答","plan":[]}',
                GatewayUsage(2, 2),
                1,
            ),
            GatewayResult(
                "deepseek",
                "deepseek-standard-test",
                '{"action":"unknown","answer":"回答","plan":[]}',
                GatewayUsage(2, 2),
                1,
            ),
        ]
    )
    published: list[tuple[str, dict[str, object]]] = []

    async def capture_event(event_type: str, payload: dict[str, object]) -> None:
        published.append((event_type, dict(payload)))

    request = build_agent_model_request(task_run_input(task), tool_schemas=())
    async with sessionmaker() as session:
        model = AgentGatewayModel(
            session=session,
            settings=Settings(),
            adapter=adapter,
            event_sink=capture_event,
        )
        with pytest.raises(AgentDecisionError, match="action is invalid"):
            await model.decide(request)

    assert published[-1] == (
        "task.phase.failed",
        {
            "phase": "answer_generation",
            "attempt": 2,
            "reason_code": "structured_decision_contract_invalid",
            "retryable": True,
        },
    )
