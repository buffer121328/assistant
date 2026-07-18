from __future__ import annotations

from pathlib import Path

from .loader import (
    EvaluationDataError,
    load_baseline,
    load_candidate_outputs,
    load_cases,
)
from .metrics import DeterministicRubricMetric
from .models import EvaluationReport, EvaluationResult


def evaluate_dataset(
    dataset_path: Path,
    baseline_path: Path,
    candidate_path: Path | None = None,
) -> EvaluationReport:
    cases = load_cases(dataset_path)
    baseline = load_baseline(baseline_path)
    candidate_outputs = (
        load_candidate_outputs(candidate_path) if candidate_path is not None else {}
    )
    case_ids = {case.id for case in cases}
    unknown_candidate_ids = sorted(set(candidate_outputs) - case_ids)
    if unknown_candidate_ids:
        raise EvaluationDataError(
            "candidate outputs contain unknown case ids: "
            + ", ".join(unknown_candidate_ids)
        )

    results: list[EvaluationResult] = []
    regressions: list[str] = []
    for case in cases:
        actual_output = candidate_outputs.get(case.id, case.actual_output)
        metric = DeterministicRubricMetric(case.rubric)
        rubric_score = metric.measure(actual_output)
        score = rubric_score.score
        baseline_score = baseline.scores.get(case.id)
        delta = (
            round(score - baseline_score, 6)
            if baseline_score is not None
            else None
        )
        if baseline_score is None or score < baseline_score:
            regressions.append(case.id)

        results.append(
            EvaluationResult(
                case_id=case.id,
                task_type=case.task_type,
                score=score,
                threshold=case.rubric.threshold,
                passed=rubric_score.passed,
                reason=rubric_score.reason,
                baseline_score=baseline_score,
                delta=delta,
            )
        )

    return EvaluationReport(
        baseline_version=baseline.version,
        passed=all(result.passed for result in results) and not regressions,
        results=tuple(results),
        regressions=tuple(regressions),
    )
