from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from evaluation import (
    DeterministicRubricMetric,
    EvaluationCase,
    load_cases,
    run_core_command_langfuse_experiment,
)


DATASET = Path(__file__).parent / "datasets/core_commands.json"
BASELINE = Path(__file__).parent / "baselines/v2-05.json"
CASES = load_cases(DATASET)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_golden_command_output_meets_deterministic_rubric(
    case: EvaluationCase,
) -> None:
    metric = DeterministicRubricMetric(case.rubric)
    result = metric.measure(case.actual_output)

    assert result.score == 1.0
    assert result.passed
    assert result.reason == "all deterministic rubric checks passed"


def test_core_command_dataset_can_be_published_as_langfuse_experiment() -> None:
    calls: list[dict[str, Any]] = []

    class Client:
        def run_experiment(self, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            task = cast(Callable[..., dict[str, object]], kwargs["task"])
            data = cast(list[dict[str, object]], kwargs["data"])
            first = task(item=data[0])
            return {"first": first, "data_count": len(data)}

    result = run_core_command_langfuse_experiment(
        client=Client(),
        dataset_path=DATASET,
        baseline_path=BASELINE,
        name="assistant.core_commands",
        metadata={"suite": "core_commands"},
    )

    assert result == {
        "first": {
            "case_id": "plan-basic",
            "task_type": "plan",
            "score": 1.0,
            "passed": True,
            "reason": "all deterministic rubric checks passed",
            "baseline_score": 1.0,
            "delta": 0.0,
        },
        "data_count": len(CASES),
    }
    assert calls[0]["name"] == "assistant.core_commands"
    assert calls[0]["run_name"] == "core_commands"
    assert calls[0]["metadata"] == {
        "suite": "core_commands",
        "dataset": "core_commands.json",
        "baseline_version": "v2-05",
    }
    first_item = calls[0]["data"][0]
    assert first_item["metadata"]["case_id"] == "plan-basic"
    assert first_item["metadata"]["actual_output"].startswith("目标:")
