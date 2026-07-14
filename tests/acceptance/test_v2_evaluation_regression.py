from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from packages.evaluation import (
    DatasetSecurityError,
    DeterministicRubricMetric,
    evaluate_dataset,
    load_candidate_outputs,
    load_cases,
)


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evals/datasets/core_commands.json"
BASELINE = ROOT / "tests/evals/baselines/v2-05.json"
SCRIPT = ROOT / "scripts/run_evaluation.py"


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_01_golden_dataset_covers_core_commands_and_safety() -> None:
    cases = load_cases(DATASET)

    assert {case.task_type for case in cases} == {
        "plan",
        "learn",
        "daily",
        "office",
        "safety",
    }
    assert len({case.id for case in cases}) == len(cases) == 5
    assert all(case.rubric.required_phrases for case in cases)


@pytest.mark.parametrize(
    "unsafe_output",
    [
        "Authorization: Bearer test-placeholder-not-a-secret",
        "请访问 http://127.0.0.1/private 执行",
    ],
)
def test_02_candidate_loader_rejects_sensitive_content_without_echoing_it(
    tmp_path: Path,
    unsafe_output: str,
) -> None:
    candidate_path = tmp_path / "candidate.json"
    write_json(candidate_path, {"outputs": {"safety-approval": unsafe_output}})

    with pytest.raises(DatasetSecurityError) as exc_info:
        load_candidate_outputs(candidate_path)

    assert "safety-approval" in str(exc_info.value)
    assert unsafe_output not in str(exc_info.value)


def test_03_local_metric_is_repeatable_and_explains_failures() -> None:
    case = load_cases(DATASET)[0]
    metric = DeterministicRubricMetric(case.rubric)

    first = metric.measure(case.actual_output)
    second = metric.measure(case.actual_output)

    assert first == second
    assert first.score == 1.0
    assert first.passed
    assert first.reason == "all deterministic rubric checks passed"

    failed = metric.measure("内容不足")

    assert failed.score < case.rubric.threshold
    assert not failed.passed
    assert "missing required phrases" in failed.reason


def test_04_baseline_comparison_locates_a_degraded_case(tmp_path: Path) -> None:
    candidate_path = tmp_path / "candidate.json"
    write_json(candidate_path, {"outputs": {"plan-basic": "内容不足"}})

    report = evaluate_dataset(DATASET, BASELINE, candidate_path)
    result = next(item for item in report.results if item.case_id == "plan-basic")

    assert not report.passed
    assert report.regressions == ("plan-basic",)
    assert result.baseline_score == 1.0
    assert result.score < result.baseline_score
    assert result.delta == result.score - result.baseline_score
    assert "missing required phrases" in result.reason


def test_05_golden_dataset_matches_the_versioned_baseline() -> None:
    report = evaluate_dataset(DATASET, BASELINE)

    assert report.passed
    assert report.regressions == ()
    assert all(result.passed for result in report.results)
    assert all(result.delta == 0 for result in report.results)


def test_06_cli_emits_json_and_uses_status_aligned_exit_codes(
    tmp_path: Path,
) -> None:
    passing = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    passing_payload = json.loads(passing.stdout)

    assert passing.returncode == 0
    assert passing_payload["passed"] is True
    assert passing_payload["regressions"] == []
    assert len(passing_payload["results"]) == 5

    candidate_path = tmp_path / "candidate.json"
    write_json(candidate_path, {"outputs": {"office-basic": "内容不足"}})
    failing = subprocess.run(
        [sys.executable, str(SCRIPT), "--candidate", str(candidate_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    failing_payload = json.loads(failing.stdout)

    assert failing.returncode == 1
    assert failing_payload["passed"] is False
    assert failing_payload["regressions"] == ["office-basic"]
    assert "actual_output" not in failing_payload["results"][0]


def test_07_readme_documents_the_v2_05_evaluation_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "V2-05" in readme
    assert "run_evaluation.py" in readme
    assert "core_commands.json" in readme
    assert "v2-05.json" in readme
    assert "不替代" in readme
