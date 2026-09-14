from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from observability.experiments import run_langfuse_dataset_experiment
from evaluation.loader import (
    EvaluationDataError,
    load_baseline,
    load_candidate_outputs,
    load_cases,
)
from evaluation.metrics import DeterministicRubricMetric
from evaluation.models import EvaluationBaseline, EvaluationCase, EvaluationRubric


class LangfuseExperimentClient(Protocol):
    """表示 处理 langfuse experiment client 的后端数据结构或服务对象。"""

    def run_experiment(self, **kwargs: Any) -> Any:
        """运行 experiment。

        Args:
            kwargs: kwargs 参数。
        """
        ...


def run_core_command_langfuse_experiment(
    *,
    client: LangfuseExperimentClient,
    dataset_path: Path,
    baseline_path: Path,
    candidate_path: Path | None = None,
    name: str = "assistant.core_commands",
    metadata: Mapping[str, str] | None = None,
) -> Any:
    """运行 core command langfuse experiment。

    Args:
        client: client 参数。
        dataset_path: dataset_path 参数。
        baseline_path: baseline_path 参数。
        candidate_path: candidate_path 参数。
        name: name 参数。
        metadata: metadata 参数。
    """
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

    return run_langfuse_dataset_experiment(
        client=client,
        name=name,
        items=_build_items(
            cases, baseline=baseline, candidate_outputs=candidate_outputs
        ),
        task=_score_case,
        scores=_build_scores(
            cases, baseline=baseline, candidate_outputs=candidate_outputs
        ),
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
    """执行 构建 items 的内部辅助逻辑。

    Args:
        cases: cases 参数。
        baseline: baseline 参数。
        candidate_outputs: candidate_outputs 参数。
    """
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


def _build_scores(
    cases: tuple[EvaluationCase, ...],
    *,
    baseline: EvaluationBaseline,
    candidate_outputs: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    """从运行结果构建可发布的评测评分，并限制字段形状。

    Args:
        cases: 用于执行当前操作的 cases 参数。
        baseline: 用于执行当前操作的 baseline 参数。
        candidate_outputs: 用于执行当前操作的 candidate outputs 参数。
    """
    scores: dict[str, dict[str, Any]] = {}
    for item in _build_items(
        cases, baseline=baseline, candidate_outputs=candidate_outputs
    ):
        result = _score_case(
            item={
                "input": item["input"],
                "expected_output": item["expected_output"],
                "metadata": {**item["metadata"], "case_id": item["id"]},
            }
        )
        scores[item["id"]] = {
            "name": "deterministic_rubric",
            "value": result["score"],
            "metadata": {
                "passed": result["passed"],
                "baseline_score": result["baseline_score"],
                "delta": result["delta"],
                "task_type": result["task_type"],
            },
        }
    return scores


def _score_case(*, item: dict[str, Any]) -> dict[str, Any]:
    """执行 处理 score case 的内部辅助逻辑。

    Args:
        item: item 参数。
    """
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
