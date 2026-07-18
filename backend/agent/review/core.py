from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import Task, ToolLog
from observability import Observability


logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter
except ModuleNotFoundError:
    class _NoopCounter:
        def labels(self, **_: object) -> _NoopCounter:
            return self

        def inc(self) -> None:
            return None

    def Counter(*_: object, **__: object) -> _NoopCounter:
        return _NoopCounter()

QUALITY_SAMPLED = Counter("agent_quality_sampled_total", "Sampled Agent outputs")
QUALITY_EVALUATIONS = Counter(
    "agent_quality_evaluations_total",
    "Agent quality evaluation outcomes",
    ("status",),
)
QUALITY_LOW_SCORE = Counter(
    "agent_quality_low_score_total",
    "Agent quality scores below threshold",
    ("dimension",),
)


@dataclass(frozen=True)
class SamplingPolicy:
    rate: float = 0.0
    version: str = "judge-v1"

    def __post_init__(self) -> None:
        if not 0.0 <= self.rate <= 1.0:
            raise ValueError("Sampling rate must be between 0 and 1")
        if not self.version.strip() or len(self.version) > 64:
            raise ValueError("Sampling policy version is invalid")

    def should_sample(self, task_id: str) -> bool:
        if self.rate <= 0:
            return False
        if self.rate >= 1:
            return True
        digest = hashlib.sha256(
            f"{self.version}:{task_id}".encode("utf-8")
        ).digest()
        bucket = int.from_bytes(digest[:8], "big") / float(2**64)
        return bucket < self.rate


@dataclass(frozen=True)
class JudgeRequest:
    task_id: str
    user_id: str
    task_type: str
    input_text: str
    output_text: str
    policy_version: str


@dataclass(frozen=True)
class JudgeDecision:
    relevance: float
    completeness: float
    faithfulness: float
    rationale: str = ""

    def __post_init__(self) -> None:
        for value in (self.relevance, self.completeness, self.faithfulness):
            if not 0.0 <= value <= 1.0:
                raise ValueError("Judge scores must be between 0 and 1")
        if len(self.rationale) > 1_000:
            raise ValueError("Judge rationale is too long")


class JudgeModel(Protocol):
    async def evaluate(self, request: JudgeRequest) -> JudgeDecision: ...


class QualityEvaluator:
    def __init__(
        self,
        *,
        sampling: SamplingPolicy,
        judge: JudgeModel,
        observability: Observability,
        threshold: float = 0.6,
        max_input_chars: int = 10_000,
        max_output_chars: int = 20_000,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("Quality threshold must be between 0 and 1")
        self.sampling = sampling
        self.judge = judge
        self.observability = observability
        self.threshold = threshold
        self.max_input_chars = max(1_000, min(max_input_chars, 20_000))
        self.max_output_chars = max(1_000, min(max_output_chars, 50_000))

    async def evaluate_task(
        self,
        *,
        session: AsyncSession,
        task: Task,
    ) -> JudgeDecision | None:
        if (
            task.status != "success"
            or task.task_type not in {"agent", "plan", "learn", "daily", "office"}
            or not task.result_text
            or not self.sampling.should_sample(task.id)
        ):
            return None
        tool_name = f"quality.judge:{self.sampling.version}"
        task_id = task.id
        existing = await session.scalar(
            select(ToolLog)
            .where(
                ToolLog.task_id == task_id,
                ToolLog.tool_name == tool_name,
                ToolLog.status == "succeeded",
            )
            .limit(1)
        )
        if existing is not None and existing.output_text:
            return _parse_stored_decision(existing.output_text)

        QUALITY_SAMPLED.inc()
        request = JudgeRequest(
            task_id=task_id,
            user_id=task.user_id,
            task_type=task.task_type,
            input_text=task.input_text[: self.max_input_chars],
            output_text=task.result_text[: self.max_output_chars],
            policy_version=self.sampling.version,
        )
        try:
            with self.observability.observe(
                "agent.quality.judge",
                as_type="evaluator",
                input={"task_id": task_id, "policy": self.sampling.version},
                metadata={"task_id": task_id},
            ) as observation:
                decision = await self.judge.evaluate(request)
                observation.update(output={"status": "success"})
            scores = {
                "relevance": decision.relevance,
                "completeness": decision.completeness,
                "faithfulness": decision.faithfulness,
            }
            for dimension, value in scores.items():
                self.observability.score(
                    name=f"judge.{dimension}",
                    value=value,
                    data_type="NUMERIC",
                    metadata={
                        "task_id": task_id,
                        "policy_version": self.sampling.version,
                    },
                )
                if value < self.threshold:
                    QUALITY_LOW_SCORE.labels(dimension=dimension).inc()
                    logger.warning(
                        "agent_quality_threshold_crossed task_id=%s dimension=%s",
                        task_id,
                        dimension,
                    )
            session.add(
                ToolLog(
                    task_id=task_id,
                    tool_name=tool_name,
                    status="succeeded",
                    input_text=json.dumps(
                        {"policy_version": self.sampling.version},
                        separators=(",", ":"),
                    ),
                    output_text=json.dumps(
                        {
                            **scores,
                            "rationale": decision.rationale,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
            QUALITY_EVALUATIONS.labels(status="succeeded").inc()
            await session.commit()
            return decision
        except Exception as exc:
            await session.rollback()
            session.add(
                ToolLog(
                    task_id=task_id,
                    tool_name=tool_name,
                    status="failed",
                    input_text=json.dumps(
                        {"policy_version": self.sampling.version},
                        separators=(",", ":"),
                    ),
                    error_message=type(exc).__name__,
                )
            )
            QUALITY_EVALUATIONS.labels(status="failed").inc()
            await session.commit()
            return None


def _parse_stored_decision(value: str) -> JudgeDecision | None:
    try:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            return None
        return JudgeDecision(
            relevance=float(payload["relevance"]),
            completeness=float(payload["completeness"]),
            faithfulness=float(payload["faithfulness"]),
            rationale=str(payload.get("rationale", ""))[:1_000],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
