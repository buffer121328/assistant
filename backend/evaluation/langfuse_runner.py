from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from .experiments import run_langfuse_experiment
from .loader import (
    EvaluationDataError,
    load_baseline,
    load_candidate_outputs,
    load_cases,
)
from .metrics import DeterministicRubricMetric
from .models import EvaluationBaseline, EvaluationCase, EvaluationRubric


class LangfuseExperimentClient(Protocol):
    def run_experiment(self, **kwargs: Any) -> Any: ...


def run_core_command_langfuse_experiment(
    *,
    client: LangfuseExperimentClient,
    dataset_path: Path,
    baseline_path: Path,
    candidate_path: Path | None = None,
    name: str = "assistant.core_commands",
    metadata: Mapping[str, str] | None = None,
) -> Any:
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

    experiment_metadata = {
        "suite": "core_commands",
        "dataset": dataset_path.name,
        "baseline_version": baseline.version,
    }
    if metadata is not None:
        experiment_metadata.update(metadata)

    return run_langfuse_experiment(
        client=client,
        name=name,
        items=_build_items(cases, baseline=baseline, candidate_outputs=candidate_outputs),
        task=_score_case,
        metadata=experiment_metadata,
        run_name=dataset_path.stem,
        description="Deterministic rubric evaluation for core command fixtures.",
    )


def _build_items(
    cases: tuple[EvaluationCase, ...],
    *,
    baseline: EvaluationBaseline,
    candidate_outputs: Mapping[str, str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for case in cases:
        actual_output = candidate_outputs.get(case.id, case.actual_output)
        items.append(
            {
                "id": case.id,
                "input": case.input,
                "expected_output": case.actual_output,
                "metadata": {
                    "case_id": case.id,
                    "task_type": case.task_type,
                    "actual_output": actual_output,
                    "rubric": asdict(case.rubric),
                    "baseline_score": baseline.scores.get(case.id),
                },
            }
        )
    return items


def _score_case(*, item: dict[str, Any]) -> dict[str, Any]:
    metadata = item["metadata"]
    rubric = EvaluationRubric(
        required_phrases=tuple(metadata["rubric"]["required_phrases"]),
        forbidden_phrases=tuple(metadata["rubric"]["forbidden_phrases"]),
        min_length=int(metadata["rubric"]["min_length"]),
        max_length=int(metadata["rubric"]["max_length"]),
        threshold=float(metadata["rubric"]["threshold"]),
    )
    score = DeterministicRubricMetric(rubric).measure(metadata["actual_output"])
    baseline_score = metadata["baseline_score"]
    delta = (
        round(score.score - float(baseline_score), 6)
        if baseline_score is not None
        else None
    )
    return {
        "case_id": metadata["case_id"],
        "task_type": metadata["task_type"],
        "score": score.score,
        "passed": score.passed,
        "reason": score.reason,
        "baseline_score": baseline_score,
        "delta": delta,
    }
