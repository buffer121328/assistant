from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Literal, Protocol

from model_gateway import GatewayMessage, sanitize_text

from agent.modeling.executors import AgentRunInput


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
    """表示 处理 agent decision error 的后端数据结构或服务对象。"""

    pass


@dataclass(frozen=True)
class AgentToolCall:
    """表示 处理 agent tool call 的后端数据结构或服务对象。"""

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentDecision:
    """表示 处理 agent decision 的后端数据结构或服务对象。"""

    action: AgentAction
    plan: tuple[str, ...] = ()
    answer: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    tool_calls: tuple[AgentToolCall, ...] = ()


@dataclass(frozen=True)
class WorkPlanStep:
    """表示 处理 work plan step 的后端数据结构或服务对象。"""

    objective: str
    acceptance_criteria: tuple[str, ...]
    agent_role: str | None = None


@dataclass(frozen=True)
class WorkPlan:
    """表示 处理 work plan 的后端数据结构或服务对象。"""

    goal: str
    steps: tuple[WorkPlanStep, ...]


@dataclass(frozen=True)
class ReviewDecision:
    """表示 处理 review decision 的后端数据结构或服务对象。"""

    status: ReviewStatus
    feedback: str


@dataclass(frozen=True)
class AgentModelRequest:
    """表示 处理 agent model request 的后端数据结构或服务对象。"""

    task_id: str
    user_id: str
    task_type: str
    messages: tuple[GatewayMessage, ...]
    stream_answer: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentModelProtocol(Protocol):
    """表示 处理 agent model protocol 的后端数据结构或服务对象。"""

    async def create_plan(self, request: AgentModelRequest) -> WorkPlan:
        """创建 plan。

        Args:
            request: request 参数。
        """
        ...

    async def decide(self, request: AgentModelRequest) -> AgentDecision:
        """处理 decide。

        Args:
            request: request 参数。
        """
        ...

    async def review(self, request: AgentModelRequest) -> ReviewDecision:
        """处理 review。

        Args:
            request: request 参数。
        """
        ...


def parse_agent_decision(value: str) -> AgentDecision:
    """解析 agent decision。

    Args:
        value: value 参数。
    """
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
            if not isinstance(call_id, str) or not _TOOL_NAME_PATTERN.fullmatch(
                call_id.strip()
            ):
                raise AgentDecisionError("Tool batch item requires a valid id")
            if call_id.strip() in call_ids:
                raise AgentDecisionError("Tool batch call ids must be unique")
            if not isinstance(tool_name, str) or not _TOOL_NAME_PATTERN.fullmatch(
                tool_name.strip()
            ):
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
    """解析 work plan。

    Args:
        value: value 参数。
    """
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
    """解析 review decision。

    Args:
        value: value 参数。
    """
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
    prompt_builder: Any | None = None,
) -> AgentModelRequest:
    """构建 agent model request。

    Args:
        run_input: run_input 参数。
        tool_schemas: tool_schemas 参数。
        history: history 参数。
        work_plan: work_plan 参数。
        sensitive_values: sensitive_values 参数。
        prompt_builder: prompt_builder 参数。
    """
    plan = run_input.plan
    context = run_input.context
    system_payload = {
        "profile": plan.profile_name,
        "goal": plan.goal,
        "default_plan_guidance": list(plan.steps),
        "max_steps": plan.max_steps,
        "timeout_seconds": plan.timeout_seconds,
        "output_format": plan.output_format,
        "memory_blocks": list(context.memory_blocks),
        "conversation_summary": context.conversation_summary,
        "memory_summary": context.memory_summary,
        "context_trace": list(context.context_trace),
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
    prompt_metadata: dict[str, Any] = {}
    if prompt_builder is not None:
        built_prompt = prompt_builder.build(system_payload)
        system_text = built_prompt.system_prompt
        prompt_metadata = built_prompt.metadata
    else:
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
        )
    ]
    for conversation_role, conversation_content in context.conversation_history:
        if conversation_role not in {"user", "assistant"} or not conversation_content:
            continue
        messages.append(
            GatewayMessage(
                role=conversation_role,
                content=sanitize_text(
                    conversation_content, extra_sensitive_values=sensitive_values
                ),
            )
        )
    messages.append(
        GatewayMessage(
            role="user",
            content=sanitize_text(
                context.input_text,
                extra_sensitive_values=sensitive_values,
            ),
        )
    )
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
            prefix = (
                f"工具结果 {name}: " if isinstance(name, str) and name else "执行结果: "
            )
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
        stream_answer=run_input.plan.execution_mode == "react",
        metadata=prompt_metadata,
    )


def build_work_plan_request(
    run_input: AgentRunInput,
    *,
    sensitive_values: tuple[str | None, ...] = (),
) -> AgentModelRequest:
    """构建 work plan request。

    Args:
        run_input: run_input 参数。
        sensitive_values: sensitive_values 参数。
    """
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
        "memory_blocks": list(context.memory_blocks),
        "conversation_summary": context.conversation_summary,
        "memory_summary": context.memory_summary,
        "context_trace": list(context.context_trace),
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
    """构建 review model request。

    Args:
        run_input: run_input 参数。
        work_plan: work_plan 参数。
        candidate_result: candidate_result 参数。
        sensitive_values: sensitive_values 参数。
    """
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
    """执行 解析 display plan 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
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
    """执行 解析 json object 的内部辅助逻辑。

    Args:
        value: value 参数。
        label: label 参数。
    """
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
    """执行 处理 bounded text 的内部辅助逻辑。

    Args:
        value: value 参数。
        label: label 参数。
        max_length: max_length 参数。
    """
    if not isinstance(value, str):
        raise AgentDecisionError(f"{label} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise AgentDecisionError(f"{label} is invalid")
    return normalized


def _work_plan_payload(work_plan: WorkPlan) -> dict[str, Any]:
    """执行 处理 work plan payload 的内部辅助逻辑。

    Args:
        work_plan: work_plan 参数。
    """
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
    """执行 处理 phase request 的内部辅助逻辑。

    Args:
        run_input: run_input 参数。
        system_text: system_text 参数。
        sensitive_values: sensitive_values 参数。
    """
    context = run_input.context
    messages = [
        GatewayMessage(
            role="system",
            content=sanitize_text(system_text, extra_sensitive_values=sensitive_values),
        )
    ]
    for conversation_role, conversation_content in context.conversation_history:
        if conversation_role in {"user", "assistant"} and conversation_content:
            messages.append(
                GatewayMessage(
                    role=conversation_role,
                    content=sanitize_text(
                        conversation_content, extra_sensitive_values=sensitive_values
                    ),
                )
            )
    messages.append(
        GatewayMessage(
            role="user",
            content=sanitize_text(
                context.input_text, extra_sensitive_values=sensitive_values
            ),
        )
    )
    return AgentModelRequest(
        task_id=context.task_id,
        user_id=context.user_id,
        task_type=context.task_type,
        messages=tuple(messages),
    )


def _safe_json(
    payload: dict[str, Any],
    sensitive_values: tuple[str | None, ...],
) -> str:
    """执行 处理 safe json 的内部辅助逻辑。

    Args:
        payload: payload 参数。
        sensitive_values: sensitive_values 参数。
    """
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
