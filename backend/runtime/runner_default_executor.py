from __future__ import annotations

from agent.modeling.executors import AgentRunInput, AgentRunResult
from agent.planning.context import TaskContext
from agent.planning.planner import ExecutionPlan


class MinimalLangGraphExecutor:
    """Fallback executor used by lightweight harness tests and local flows."""

    async def execute(self, *, run_input: AgentRunInput) -> AgentRunResult:
        plan = run_input.plan
        context = run_input.context
        tool_calls = ("search.web",) if context.sources else ()
        return AgentRunResult(
            result_text=build_langgraph_result(plan, context),
            tool_calls=tool_calls,
            loop_steps=min(len(plan.steps), plan.max_steps),
            checkpoint_id=f"ckpt-{context.task_id[:8]}",
        )


def build_langgraph_result(plan: ExecutionPlan, context: TaskContext) -> str:
    lines = [f"目标: {plan.goal}", "", "执行步骤:"]
    for index, step in enumerate(plan.steps, start=1):
        lines.append(f"{index}. {step}")

    if context.memory_summary:
        lines.extend(["", f"记忆摘要: {context.memory_summary}"])

    if context.sources:
        lines.extend(["", "参考来源:"])
        for source in context.sources:
            title = source.get("title") or source.get("url") or "来源"
            url = source.get("url")
            lines.append(f"- {title}" + (f" - {url}" if url else ""))

    return "\n".join(lines)


__all__ = ["MinimalLangGraphExecutor", "build_langgraph_result"]
