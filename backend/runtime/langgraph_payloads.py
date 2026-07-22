from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from runtime.langgraph_state import _ExecutionState

if TYPE_CHECKING:
    from agent.modeling.agent_model import AgentDecision, WorkPlan
    from agent.modeling.executors import HumanApprovalRequest


def _decision_payload(decision: AgentDecision) -> dict[str, Any]:
    """执行 处理 decision payload 的内部辅助逻辑。

    Args:
        decision: decision 参数。
    """
    return {
        "action": decision.action,
        "answer": decision.answer,
        "tool_name": decision.tool_name,
        "arguments": decision.arguments,
        "plan": list(decision.plan),
        "tool_calls": [
            {
                "id": call.call_id,
                "tool_name": call.tool_name,
                "arguments": call.arguments,
            }
            for call in decision.tool_calls
        ],
    }


def _approval_requests_from_interrupts(
    interrupts: tuple[Any, ...],
) -> tuple[HumanApprovalRequest, ...]:
    """执行 处理 approval requests from interrupts 的内部辅助逻辑。

    Args:
        interrupts: interrupts 参数。
    """
    from agent.modeling.executors import ApprovalTypeName, HumanApprovalRequest

    requests: list[HumanApprovalRequest] = []
    for item in interrupts:
        value = getattr(item, "value", None)
        if not isinstance(value, dict):
            continue
        approval_type = value.get("approval_type")
        if approval_type not in {"tool", "plan", "review"}:
            approval_type = "tool" if value.get("tool_name") else None
        subject = value.get("subject") or value.get("tool_name")
        summary = value.get("summary") or "需要人工审批。"
        if (
            approval_type in {"tool", "plan", "review"}
            and isinstance(subject, str)
            and isinstance(summary, str)
        ):
            request = HumanApprovalRequest(
                approval_type=cast(ApprovalTypeName, approval_type),
                subject=subject[:128],
                summary=summary[:1000],
                tool_name=(
                    value.get("tool_name")
                    if isinstance(value.get("tool_name"), str)
                    else None
                ),
            )
            if request not in requests:
                requests.append(request)
    return tuple(requests)


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


def _work_plan_from_state(state: _ExecutionState) -> WorkPlan | None:
    """执行 处理 work plan from state 的内部辅助逻辑。

    Args:
        state: state 参数。
    """
    from agent.modeling.agent_model import WorkPlan, WorkPlanStep

    payload = state.get("work_plan")
    if not isinstance(payload, dict):
        return None
    goal = payload.get("goal")
    raw_steps = payload.get("steps")
    if not isinstance(goal, str) or not isinstance(raw_steps, list):
        return None
    steps: list[WorkPlanStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            return None
        objective = item.get("objective")
        criteria = item.get("acceptance_criteria")
        agent_role = item.get("agent_role")
        if not isinstance(objective, str) or not isinstance(criteria, list):
            return None
        if not all(isinstance(value, str) for value in criteria):
            return None
        if agent_role is not None and not isinstance(agent_role, str):
            return None
        steps.append(
            WorkPlanStep(
                objective=objective,
                acceptance_criteria=tuple(criteria),
                agent_role=agent_role,
            )
        )
    return WorkPlan(goal=goal, steps=tuple(steps))


def _work_plan_summary(state: _ExecutionState) -> str:
    """执行 处理 work plan summary 的内部辅助逻辑。

    Args:
        state: state 参数。
    """
    work_plan = _work_plan_from_state(state)
    if work_plan is None:
        raise RuntimeError("Work plan is unavailable for approval")
    return "；".join(step.objective for step in work_plan.steps)[:1000]
