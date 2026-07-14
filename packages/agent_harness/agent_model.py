from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Literal, Protocol

from packages.model_gateway import GatewayMessage, sanitize_text

from .executors import AgentRunInput


AgentAction = Literal["final", "tool_call", "tool_batch"]
ReviewStatus = Literal["pass", "retry", "replan", "escalate", "fail"]
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_DISPLAY_PLAN_STEPS = 5
_MAX_DISPLAY_PLAN_STEP_LENGTH = 200
_MAX_WORK_PLAN_STEPS = 5
_MAX_ACCEPTANCE_CRITERIA = 5
_MAX_REVIEW_FEEDBACK_LENGTH = 500
_MAX_TOOL_BATCH_SIZE = 3
_ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class AgentDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class AgentToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentDecision:
    action: AgentAction
    plan: tuple[str, ...] = ()
    answer: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    tool_calls: tuple[AgentToolCall, ...] = ()


@dataclass(frozen=True)
class WorkPlanStep:
    objective: str
    acceptance_criteria: tuple[str, ...]
    agent_role: str | None = None


@dataclass(frozen=True)
class WorkPlan:
    goal: str
    steps: tuple[WorkPlanStep, ...]


@dataclass(frozen=True)
class ReviewDecision:
    status: ReviewStatus
    feedback: str


@dataclass(frozen=True)
class AgentModelRequest:
    task_id: str
    user_id: str
    task_type: str
    messages: tuple[GatewayMessage, ...]


class AgentModelProtocol(Protocol):
    async def create_plan(self, request: AgentModelRequest) -> WorkPlan: ...

    async def decide(self, request: AgentModelRequest) -> AgentDecision: ...

    async def review(self, request: AgentModelRequest) -> ReviewDecision: ...


def parse_agent_decision(value: str) -> AgentDecision:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgentDecisionError("Agent decision must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise AgentDecisionError("Agent decision must be a JSON object")

    action = payload.get("action")
    if action not in {"final", "tool_call", "tool_batch"}:
        raise AgentDecisionError("Agent decision action is invalid")
    plan = _parse_display_plan(payload.get("plan", []))

    if action == "final":
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise AgentDecisionError("Final agent decision requires a non-empty answer")
        if (
            payload.get("tool_name") is not None
            or payload.get("arguments") is not None
            or payload.get("tool_calls") is not None
        ):
            raise AgentDecisionError("Final agent decision must not request a tool")
        return AgentDecision(
            action="final",
            plan=plan,
            answer=answer.strip(),
        )

    if payload.get("answer") not in {None, ""}:
        raise AgentDecisionError("Tool decision must not contain a final answer")
    if action == "tool_batch":
        if payload.get("tool_name") is not None or payload.get("arguments") is not None:
            raise AgentDecisionError("Tool batch must not contain a single tool call")
        raw_calls = payload.get("tool_calls")
        if (
            not isinstance(raw_calls, list)
            or not raw_calls
            or len(raw_calls) > _MAX_TOOL_BATCH_SIZE
        ):
            raise AgentDecisionError("Tool batch size is invalid")
        calls: list[AgentToolCall] = []
        call_ids: set[str] = set()
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                raise AgentDecisionError("Tool batch item must be an object")
            call_id = raw_call.get("id")
            tool_name = raw_call.get("tool_name")
            arguments = raw_call.get("arguments")
            if not isinstance(call_id, str) or not _TOOL_NAME_PATTERN.fullmatch(call_id.strip()):
                raise AgentDecisionError("Tool batch item requires a valid id")
            if call_id.strip() in call_ids:
                raise AgentDecisionError("Tool batch call ids must be unique")
            if not isinstance(tool_name, str) or not _TOOL_NAME_PATTERN.fullmatch(tool_name.strip()):
                raise AgentDecisionError("Tool batch item requires a valid tool name")
            if not isinstance(arguments, dict):
                raise AgentDecisionError("Tool batch arguments must be an object")
            call_ids.add(call_id.strip())
            calls.append(AgentToolCall(call_id.strip(), tool_name.strip(), arguments))
        return AgentDecision(action="tool_batch", plan=plan, tool_calls=tuple(calls))
    if payload.get("tool_calls") is not None:
        raise AgentDecisionError("Single tool decision must not contain a tool batch")
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not _TOOL_NAME_PATTERN.fullmatch(
        tool_name.strip()
    ):
        raise AgentDecisionError("Tool decision requires a valid tool name")
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        raise AgentDecisionError("Tool decision arguments must be an object")
    return AgentDecision(
        action="tool_call",
        plan=plan,
        tool_name=tool_name.strip(),
        arguments=arguments,
    )


def parse_work_plan(value: str) -> WorkPlan:
    payload = _parse_json_object(value, "Work plan")
    goal = _bounded_text(payload.get("goal"), "Work plan goal")
    raw_steps = payload.get("steps")
    if (
        not isinstance(raw_steps, list)
        or not raw_steps
        or len(raw_steps) > _MAX_WORK_PLAN_STEPS
    ):
        raise AgentDecisionError("Work plan steps are invalid")
    steps: list[WorkPlanStep] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise AgentDecisionError("Work plan step must be an object")
        objective = _bounded_text(
            raw_step.get("objective"),
            "Work plan objective",
        )
        raw_criteria = raw_step.get("acceptance_criteria")
        if (
            not isinstance(raw_criteria, list)
            or not raw_criteria
            or len(raw_criteria) > _MAX_ACCEPTANCE_CRITERIA
        ):
            raise AgentDecisionError("Work plan acceptance criteria are invalid")
        criteria = tuple(
            _bounded_text(item, "Work plan acceptance criterion")
            for item in raw_criteria
        )
        raw_role = raw_step.get("agent_role")
        if raw_role is not None and (
            not isinstance(raw_role, str)
            or not _ROLE_PATTERN.fullmatch(raw_role.strip())
        ):
            raise AgentDecisionError("Work plan agent role is invalid")
        steps.append(
            WorkPlanStep(
                objective=objective,
                acceptance_criteria=criteria,
                agent_role=(raw_role.strip() if isinstance(raw_role, str) else None),
            )
        )
    return WorkPlan(goal=goal, steps=tuple(steps))


def parse_review_decision(value: str) -> ReviewDecision:
    payload = _parse_json_object(value, "Review decision")
    status = payload.get("status")
    if status not in {"pass", "retry", "replan", "escalate", "fail"}:
        raise AgentDecisionError("Review decision status is invalid")
    feedback = _bounded_text(
        payload.get("feedback"),
        "Review feedback",
        max_length=_MAX_REVIEW_FEEDBACK_LENGTH,
    )
    return ReviewDecision(status=status, feedback=feedback)


def build_agent_model_request(
    run_input: AgentRunInput,
    *,
    tool_schemas: tuple[dict[str, Any], ...],
    history: tuple[dict[str, Any], ...] = (),
    work_plan: WorkPlan | None = None,
    sensitive_values: tuple[str | None, ...] = (),
) -> AgentModelRequest:
    plan = run_input.plan
    context = run_input.context
    system_payload = {
        "profile": plan.profile_name,
        "goal": plan.goal,
        "default_plan_guidance": list(plan.steps),
        "max_steps": plan.max_steps,
        "timeout_seconds": plan.timeout_seconds,
        "output_format": plan.output_format,
        "memory_summary": context.memory_summary,
        "skills": [
            {"name": name, "instructions": instructions}
            for name, instructions in zip(
                context.skill_names,
                context.skill_instructions,
                strict=False,
            )
        ],
        "capabilities": list(context.capability_summary),
        "tools": list(tool_schemas),
        "work_plan": _work_plan_payload(work_plan) if work_plan else None,
    }
    system_text = (
        "你是受控的个人 Agent Core。只能在给定目标、步数、超时和工具范围内工作。\n"
        "每轮只输出一个 JSON 对象：\n"
        '- 直接回答：{"action":"final","answer":"...","plan":["..."]}\n'
        '- 调用工具：{"action":"tool_call","tool_name":"...",'
        '"arguments":{},"plan":["..."]}\n'
        '- 并行工具：{"action":"tool_batch","tool_calls":['
        '{"id":"call-1","tool_name":"...","arguments":{}}],'
        '"plan":["..."]}\n'
        "plan 仅是最多 5 条面向用户的简短说明，不能扩大工具权限或执行预算。"
        "不要输出隐式思维链、凭据、配置或 JSON 之外的文本。\n"
        f"运行上下文：{_safe_json(system_payload, sensitive_values)}"
    )
    messages: list[GatewayMessage] = [
        GatewayMessage(
            role="system",
            content=sanitize_text(
                system_text,
                extra_sensitive_values=sensitive_values,
            ),
        ),
        GatewayMessage(
            role="user",
            content=sanitize_text(
                context.input_text,
                extra_sensitive_values=sensitive_values,
            ),
        ),
    ]
    for item in history:
        role = item.get("role")
        name = item.get("name")
        content = item.get("content")
        if not isinstance(content, str) or not content:
            continue
        if role == "assistant":
            message_role = "assistant"
            message_content = content
        else:
            message_role = "user"
            prefix = f"工具结果 {name}: " if isinstance(name, str) and name else "执行结果: "
            message_content = f"{prefix}{content}"
        messages.append(
            GatewayMessage(
                role=message_role,
                content=sanitize_text(
                    message_content,
                    extra_sensitive_values=sensitive_values,
                ),
            )
        )
    return AgentModelRequest(
        task_id=context.task_id,
        user_id=context.user_id,
        task_type=context.task_type,
        messages=tuple(messages),
    )


def build_work_plan_request(
    run_input: AgentRunInput,
    *,
    sensitive_values: tuple[str | None, ...] = (),
) -> AgentModelRequest:
    plan = run_input.plan
    context = run_input.context
    payload = {
        "profile": plan.profile_name,
        "goal": plan.goal,
        "default_plan_guidance": list(plan.steps),
        "allowed_tools": list(plan.allowed_tools),
        "approval_required_tools": list(plan.approval_required_tools),
        "max_steps": plan.max_steps,
        "timeout_seconds": plan.timeout_seconds,
        "output_format": plan.output_format,
        "memory_summary": context.memory_summary,
        "skills": list(context.skill_names),
    }
    system_text = (
        "为受控个人 Agent 生成面向用户的工作计划。只输出 JSON："
        '{"goal":"...","steps":[{"objective":"...",'
        '"acceptance_criteria":["..."],"agent_role":"researcher"}]}。'
        "步骤最多 5 条；计划仅提供指导，不能扩大工具、审批、步数或超时权限。"
        "agent_role 可省略，只能表示受限认知分工，不能授予工具或审批权限。"
        "不要输出隐式思维链、凭据或 JSON 之外的文本。\n"
        f"安全包络：{_safe_json(payload, sensitive_values)}"
    )
    return _phase_request(run_input, system_text, sensitive_values)


def build_review_model_request(
    run_input: AgentRunInput,
    *,
    work_plan: WorkPlan,
    candidate_result: str,
    sensitive_values: tuple[str | None, ...] = (),
) -> AgentModelRequest:
    payload = {
        "goal": run_input.plan.goal,
        "output_format": run_input.plan.output_format,
        "work_plan": _work_plan_payload(work_plan),
        "candidate_result": candidate_result,
    }
    system_text = (
        "复核候选答案是否满足目标、工作计划和验收标准。只输出 JSON："
        '{"status":"pass|retry|replan|escalate|fail","feedback":"..."}。'
        "retry 表示重做回答，replan 表示重做计划，escalate 表示需要用户确认，"
        "fail 表示无法安全完成。不要输出隐式思维链或 JSON 之外的文本。\n"
        f"复核上下文：{_safe_json(payload, sensitive_values)}"
    )
    return _phase_request(run_input, system_text, sensitive_values)


def _parse_display_plan(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > _MAX_DISPLAY_PLAN_STEPS:
        raise AgentDecisionError("Agent display plan is invalid")
    steps: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise AgentDecisionError("Agent display plan step must be text")
        step = item.strip()
        if not step or len(step) > _MAX_DISPLAY_PLAN_STEP_LENGTH:
            raise AgentDecisionError("Agent display plan step is invalid")
        steps.append(step)
    return tuple(steps)


def _parse_json_object(value: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgentDecisionError(f"{label} must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise AgentDecisionError(f"{label} must be a JSON object")
    return payload


def _bounded_text(
    value: object,
    label: str,
    *,
    max_length: int = _MAX_DISPLAY_PLAN_STEP_LENGTH,
) -> str:
    if not isinstance(value, str):
        raise AgentDecisionError(f"{label} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise AgentDecisionError(f"{label} is invalid")
    return normalized


def _work_plan_payload(work_plan: WorkPlan) -> dict[str, Any]:
    return {
        "goal": work_plan.goal,
        "steps": [
            {
                "objective": step.objective,
                "acceptance_criteria": list(step.acceptance_criteria),
                "agent_role": step.agent_role,
            }
            for step in work_plan.steps
        ],
    }


def _phase_request(
    run_input: AgentRunInput,
    system_text: str,
    sensitive_values: tuple[str | None, ...],
) -> AgentModelRequest:
    context = run_input.context
    return AgentModelRequest(
        task_id=context.task_id,
        user_id=context.user_id,
        task_type=context.task_type,
        messages=(
            GatewayMessage(
                role="system",
                content=sanitize_text(
                    system_text,
                    extra_sensitive_values=sensitive_values,
                ),
            ),
            GatewayMessage(
                role="user",
                content=sanitize_text(
                    context.input_text,
                    extra_sensitive_values=sensitive_values,
                ),
            ),
        ),
    )


def _safe_json(
    payload: dict[str, Any],
    sensitive_values: tuple[str | None, ...],
) -> str:
    return sanitize_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ),
        extra_sensitive_values=sensitive_values,
    )
