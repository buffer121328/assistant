from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent import (
    AgentModelRequest,
    AgentRunInput,
    DefaultPlanningLayer,
    DefaultProfileSelector,
    ExecutionPlan,
    TaskContext,
    WorkPlan,
    WorkPlanStep,
    build_agent_model_request,
    build_review_model_request,
    build_work_plan_request,
)
from features.types import ModelCallParameters, ModelParameterSet
from model_gateway import (
    GatewayMessage,
    GatewayRequest,
    GatewayResult,
    GatewayUsage,
)
from model_gateway import agent_model as agent_model_module
from model_gateway.agent_model import AgentGatewayModel
from model_gateway.openai_compatible import _request_timeout
from runtime.budget import BudgetExceededError, RunBudget


def _run_input(task_type: str = "office") -> AgentRunInput:
    return AgentRunInput(
        plan=ExecutionPlan(
            goal="准备办公材料",
            steps=("整理材料", "生成结果"),
            allowed_tools=(),
            approval_required_tools=(),
            max_steps=12,
            timeout_seconds=300.0,
            risk_level="low",
            output_format="markdown",
            profile_name="v2.office_writer",
            executor_kind="langgraph",
            workflow_key="langgraph.office",
            execution_mode="plan_execute_review",
            model_parameters=ModelParameterSet(
                plan=ModelCallParameters(
                    temperature=0.10,
                    max_tokens=4096,
                    timeout_seconds=60.0,
                ),
                decision=ModelCallParameters(
                    temperature=0.25,
                    max_tokens=24_576,
                    timeout_seconds=180.0,
                ),
                review=ModelCallParameters(
                    temperature=0.05,
                    max_tokens=6144,
                    timeout_seconds=90.0,
                ),
            ),
            max_input_tokens=100_000,
            max_output_tokens=100_000,
            max_total_tokens=200_000,
        ),
        context=TaskContext(
            task_id="task-1",
            user_id="user-1",
            task_type=task_type,
            input_text="生成办公材料",
            memory_summary="",
        ),
    )


def test_profiles_and_execution_plans_keep_distinct_generation_envelopes() -> None:
    selector = DefaultProfileSelector()
    planner = DefaultPlanningLayer()

    plan_task = SimpleNamespace(
        task_type="plan",
        model_class=None,
        input_text="/plan 制定计划",
    )
    office_task = SimpleNamespace(
        task_type="office",
        model_class=None,
        input_text="/office 写报告",
    )
    plan_profile = selector.select(plan_task)
    office_profile = selector.select(office_task)

    plan = planner.build_plan(
        task=plan_task,
        profile=plan_profile,
        context=TaskContext(
            task_id="plan-task",
            user_id="user-1",
            task_type="plan",
            input_text=plan_task.input_text,
            memory_summary="",
        ),
    )
    office = planner.build_plan(
        task=office_task,
        profile=office_profile,
        context=TaskContext(
            task_id="office-task",
            user_id="user-1",
            task_type="office",
            input_text=office_task.input_text,
            memory_summary="",
        ),
    )

    assert plan.model_parameters.plan != plan.model_parameters.decision
    assert office.model_parameters.plan != office.model_parameters.decision
    assert office.model_parameters.decision.max_tokens > plan.model_parameters.decision.max_tokens
    assert office.model_parameters.decision.timeout_seconds > plan.model_parameters.decision.timeout_seconds
    assert office.timeout_seconds > plan.timeout_seconds
    assert plan.max_total_tokens == office.max_total_tokens == 200_000


def test_phase_request_builders_use_trusted_phase_parameters() -> None:
    run_input = _run_input()
    work_plan = WorkPlan(
        goal="准备办公材料",
        steps=(
            WorkPlanStep(
                objective="整理材料",
                acceptance_criteria=("结构完整",),
            ),
        ),
    )

    planning_request = build_work_plan_request(run_input)
    decision_request = build_agent_model_request(
        run_input,
        tool_schemas=(),
        work_plan=work_plan,
    )
    review_request = build_review_model_request(
        run_input,
        work_plan=work_plan,
        candidate_result="候选结果",
    )

    assert (planning_request.temperature, planning_request.max_tokens, planning_request.timeout_seconds) == (
        0.10,
        4096,
        60.0,
    )
    assert (decision_request.temperature, decision_request.max_tokens, decision_request.timeout_seconds) == (
        0.25,
        24_576,
        180.0,
    )
    assert (review_request.temperature, review_request.max_tokens, review_request.timeout_seconds) == (
        0.05,
        6144,
        90.0,
    )


@pytest.mark.asyncio
async def test_agent_gateway_forwards_request_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[GatewayRequest] = []

    class Adapter:
        async def chat(self, request: GatewayRequest, model_class: str) -> GatewayResult:
            captured.append(request)
            return GatewayResult(
                provider="test",
                model="test-model",
                content='{"action":"final","answer":"完成","plan":[]}',
                usage=GatewayUsage(input_tokens=3, output_tokens=2),
                latency_ms=1,
            )

    class Repository:
        def __init__(self, _session: Any) -> None:
            pass

        async def create_model_log(self, _entry: Any) -> None:
            return None

    class Session:
        async def flush(self) -> None:
            return None

    monkeypatch.setattr(agent_model_module, "ModelLogRepository", Repository)
    model = AgentGatewayModel(
        session=cast(Any, Session()),
        settings=cast(Any, SimpleNamespace(
            deepseek_api_key=None,
            deepseek_base_url=None,
            qwen_api_key=None,
            qwen_base_url=None,
            tavily_api_key=None,
        )),
        adapter=Adapter(),
    )

    result = await model.decide(
        AgentModelRequest(
            task_id="task-1",
            user_id="user-1",
            task_type="plan",
            messages=(GatewayMessage(role="user", content="hello"),),
            temperature=0.35,
            max_tokens=777,
            timeout_seconds=123.0,
        )
    )

    assert result.answer == "完成"
    assert len(captured) == 1
    assert captured[0].temperature == 0.35
    assert captured[0].max_tokens == 777
    assert captured[0].timeout_seconds == 123.0


def test_run_budget_enforces_combined_session_token_limit() -> None:
    budget = RunBudget.from_limits(
        max_steps=12,
        max_tool_calls=15,
        max_input_tokens=150,
        max_output_tokens=100,
        max_total_tokens=200,
    )
    budget.record_model_usage(input_tokens=120, output_tokens=70)
    assert budget.summary()["used"]["total_tokens"] == 190
    assert budget.summary()["remaining"]["total_tokens"] == 10

    with pytest.raises(BudgetExceededError) as exc_info:
        budget.record_model_usage(input_tokens=1, output_tokens=10)

    assert exc_info.value.stop_reason == "token_limit_exceeded"
    assert exc_info.value.summary["limits"]["max_total_tokens"] == 200
    assert exc_info.value.summary["used"]["total_tokens"] == 201


def test_provider_timeout_prefers_request_override_and_clamps_it() -> None:
    request = GatewayRequest(
        user_id="user-1",
        task_id="task-1",
        task_type="plan",
        model_class=None,
        messages=(GatewayMessage(role="user", content="hello"),),
        temperature=0.0,
        max_tokens=10,
        timeout_seconds=120.0,
    )
    legacy_request = GatewayRequest(
        user_id="user-1",
        task_id="task-1",
        task_type="plan",
        model_class=None,
        messages=(GatewayMessage(role="user", content="hello"),),
        temperature=0.0,
        max_tokens=10,
    )

    assert _request_timeout(request, 20.0) == 120.0
    assert _request_timeout(legacy_request, 20.0) == 20.0
    assert _request_timeout(
        GatewayRequest(
            user_id="user-1",
            task_id="task-1",
            task_type="plan",
            model_class=None,
            messages=(),
            temperature=0.0,
            max_tokens=10,
            timeout_seconds=999.0,
        ),
        20.0,
    ) == 300.0
