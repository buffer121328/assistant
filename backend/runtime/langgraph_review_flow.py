from __future__ import annotations

from typing import TYPE_CHECKING, Any

from runtime.langgraph_payloads import _work_plan_from_state
from runtime.langgraph_state import _ExecutionState

if TYPE_CHECKING:
    from agent.modeling.executors import AgentRunInput
    from runtime.loop import ControlledLoop


class ReviewFlowMixin:
    """Handles review, finalization, and review failure graph nodes."""

    async def _review(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 review 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """

        async def review_candidate() -> _ExecutionState:
            """处理 review candidate。"""
            from agent.modeling.agent_model import build_review_model_request

            work_plan = _work_plan_from_state(state)
            candidate_result = state.get("candidate_result", "")
            if work_plan is None or not candidate_result:
                raise RuntimeError("Review state is unavailable")
            request = build_review_model_request(
                run_input,
                work_plan=work_plan,
                candidate_result=candidate_result,
                sensitive_values=self.sensitive_values,
            )
            decision = await self.model.review(request)
            update: _ExecutionState = {
                "review_decision": {
                    "status": decision.status,
                    "feedback": decision.feedback,
                },
                "review_feedback": decision.feedback,
            }
            if decision.status == "retry":
                retry_count = int(state.get("review_retry_count", 0))
                if retry_count >= run_input.plan.max_review_retries:
                    raise RuntimeError("Review retry budget exhausted")
                history = list(state.get("history", []))
                history.append(
                    {
                        "role": "review",
                        "name": "review.feedback",
                        "content": decision.feedback,
                    }
                )
                update.update(
                    {
                        "review_retry_count": retry_count + 1,
                        "history": history,
                        "candidate_result": "",
                    }
                )
            elif decision.status == "replan":
                replan_count = int(state.get("replan_count", 0))
                if replan_count >= run_input.plan.max_replans:
                    raise RuntimeError("Review replan budget exhausted")
                update.update(
                    {
                        "replan_count": replan_count + 1,
                        "candidate_result": "",
                    }
                )
            return update

        update = await self._run_observed_step(
            "review",
            run_input,
            lambda: loop.run_step("review", review_candidate),
        )
        update["step_count"] = loop.steps_executed
        return update

    def _route_after_review(self: Any, state: _ExecutionState) -> str:
        """执行 路由 after review 的内部辅助逻辑。

        Args:
            state: state 参数。
        """
        status = state.get("review_decision", {}).get("status")
        if status == "pass":
            return "finalize"
        if status == "retry":
            return "retry"
        if status == "replan":
            return "replan"
        if status == "escalate":
            return "human_review"
        return "fail"

    async def _finalize(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 finalize 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """

        async def finalize() -> _ExecutionState:
            """处理 finalize。"""
            candidate = state.get("candidate_result", "")
            if not candidate:
                raise RuntimeError("Reviewed candidate is unavailable")
            return {"result_text": candidate}

        update = await self._run_observed_step(
            "finalize",
            run_input,
            lambda: loop.run_step("finalize", finalize),
        )
        update["step_count"] = loop.steps_executed
        return update

    async def _fail_review(
        self: Any,
        state: _ExecutionState,
        run_input: AgentRunInput,
        loop: ControlledLoop,
    ) -> _ExecutionState:
        """执行 处理 fail review 的内部辅助逻辑。

        Args:
            state: state 参数。
            run_input: run_input 参数。
            loop: loop 参数。
        """

        async def fail() -> _ExecutionState:
            """处理 fail。"""
            feedback = state.get("review_feedback", "Review rejected candidate")
            raise RuntimeError(f"Review failed: {feedback}")

        return await self._run_observed_step(
            "review_failure",
            run_input,
            lambda: loop.run_step("review_failure", fail),
        )
