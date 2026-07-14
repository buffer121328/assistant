from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


TaskType = Literal["plan", "learn", "daily", "office", "safety"]


@dataclass(frozen=True)
class EvaluationRubric:
    required_phrases: tuple[str, ...]
    forbidden_phrases: tuple[str, ...]
    min_length: int
    max_length: int
    threshold: float


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    task_type: TaskType
    input: str
    actual_output: str
    rubric: EvaluationRubric


@dataclass(frozen=True)
class EvaluationBaseline:
    version: str
    scores: dict[str, float]


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    task_type: TaskType
    score: float
    threshold: float
    passed: bool
    reason: str
    baseline_score: float | None
    delta: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "task_type": self.task_type,
            "score": self.score,
            "threshold": self.threshold,
            "passed": self.passed,
            "reason": self.reason,
            "baseline_score": self.baseline_score,
            "delta": self.delta,
        }


@dataclass(frozen=True)
class EvaluationReport:
    baseline_version: str
    passed: bool
    results: tuple[EvaluationResult, ...]
    regressions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_version": self.baseline_version,
            "passed": self.passed,
            "results": [result.to_dict() for result in self.results],
            "regressions": list(self.regressions),
        }
