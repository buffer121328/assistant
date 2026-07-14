from __future__ import annotations

from pathlib import Path

import pytest

from packages.evaluation import (
    DeterministicRubricMetric,
    EvaluationCase,
    load_cases,
)


DATASET = Path(__file__).parent / "datasets/core_commands.json"
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
