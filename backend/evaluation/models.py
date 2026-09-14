from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


TaskType = Literal["plan", "learn", "daily", "office", "safety"]


@dataclass(frozen=True)
class EvaluationRubric:
    """表示 处理 evaluation rubric 的后端数据结构或服务对象。"""

    required_phrases: tuple[str, ...]  # required_phrases 对应的数据字段。
    forbidden_phrases: tuple[str, ...]  # forbidden_phrases 对应的数据字段。
    min_length: int  # min_length 对应的数据字段。
    max_length: int  # max_length 对应的数据字段。
    threshold: float  # threshold 对应的数据字段。


@dataclass(frozen=True)
class EvaluationCase:
    """表示 处理 evaluation case 的后端数据结构或服务对象。"""

    id: str  # id 对应的数据字段。
    task_type: TaskType  # task_type 对应的数据字段。
    input: str  # input 对应的数据字段。
    actual_output: str  # actual_output 对应的数据字段。
    rubric: EvaluationRubric  # rubric 对应的数据字段。


@dataclass(frozen=True)
class EvaluationBaseline:
    """表示 处理 evaluation baseline 的后端数据结构或服务对象。"""

    version: str  # version 对应的数据字段。
    scores: dict[str, float]  # scores 对应的数据字段。


@dataclass(frozen=True)
class EvaluationResult:
    """表示 处理 evaluation result 的后端数据结构或服务对象。"""

    case_id: str  # case_id 对应的数据字段。
    task_type: TaskType  # task_type 对应的数据字段。
    score: float  # score 对应的数据字段。
    threshold: float  # threshold 对应的数据字段。
    passed: bool  # passed 对应的数据字段。
    reason: str  # reason 对应的数据字段。
    baseline_score: float | None  # baseline_score 对应的数据字段。
    delta: float | None  # delta 对应的数据字段。

    def to_dict(self) -> dict[str, Any]:
        """转换为目标格式 dict。"""
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
    """表示 处理 evaluation report 的后端数据结构或服务对象。"""

    baseline_version: str  # baseline_version 对应的数据字段。
    passed: bool  # passed 对应的数据字段。
    results: tuple[EvaluationResult, ...]  # results 对应的数据字段。
    regressions: tuple[str, ...]  # regressions 对应的数据字段。

    def to_dict(self) -> dict[str, Any]:
        """转换为目标格式 dict。"""
        return {
            "baseline_version": self.baseline_version,
            "passed": self.passed,
            "results": [result.to_dict() for result in self.results],
            "regressions": list(self.regressions),
        }
