from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from domain.policies.redaction import sanitize_text

from runtime.langgraph_state import _AGENT_CORE_VERSION, _ExecutionState

if TYPE_CHECKING:
    from agent.ports import AgentRunInput


class RuntimeHelperMixin:
    """定义当前组件的职责和边界。"""

    async def _run_observed_step(
        self: Any,
        step_name: str,
        run_input: AgentRunInput,
        operation: Any,
    ) -> _ExecutionState:
        """执行 运行 observed step 的内部辅助逻辑。

        Args:
            step_name: step_name 参数。
            run_input: run_input 参数。
            operation: operation 参数。
        """
        with self.observability.observe(
            f"agent.graph.{step_name}",
            input={"step": step_name},
            metadata={
                "task_id": run_input.context.task_id,
                "user_id": run_input.context.user_id,
                "agent_run_id": getattr(self, "agent_run_id", None),
                "agent_core_version": _AGENT_CORE_VERSION,
                "execution_mode": run_input.plan.execution_mode,
                "step_count": None,
                "checkpoint_id": getattr(self, "_last_checkpoint_id", None),
            },
        ) as observation:
            result = cast(_ExecutionState, await operation())
            output: dict[str, Any] = {"status": "success"}
            decision = result.get("decision")
            if isinstance(decision, dict):
                action = decision.get("action")
                if isinstance(action, str):
                    output["route_action"] = action
            review_decision = result.get("review_decision")
            if isinstance(review_decision, dict):
                status = review_decision.get("status")
                if isinstance(status, str):
                    output["review_status"] = status
            if "step_count" in result:
                output["step_count"] = result["step_count"]
            observation.update(output=output)
            return result

    async def _snapshot(self: Any, graph: Any, config: dict[str, Any]) -> Any | None:
        """执行 处理 snapshot 的内部辅助逻辑。

        Args:
            graph: graph 参数。
            config: config 参数。
        """
        if self.checkpointer is None:
            return None
        return await graph.aget_state(config)

    def _checkpoint_id(self: Any, snapshot: Any | None) -> str | None:
        """执行 处理 checkpoint id 的内部辅助逻辑。

        Args:
            snapshot: snapshot 参数。
        """
        if snapshot is None:
            return None
        configurable = snapshot.config.get("configurable", {})
        checkpoint_id = configurable.get("checkpoint_id")
        return str(checkpoint_id) if checkpoint_id else None

    def _safe_json(self: Any, value: Any) -> str:
        """执行 处理 safe json 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        return sanitize_text(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ),
            extra_sensitive_values=self.sensitive_values,
        )
