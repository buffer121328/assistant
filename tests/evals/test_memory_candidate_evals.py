from pathlib import Path

from evaluation.candidate_memory import evaluate_candidate_memory_fixture


DATASET = Path(__file__).parent / "datasets/memory_candidates_v6_03.json"


def test_v6_candidate_memory_fixture_reports_required_metrics() -> None:
    report = evaluate_candidate_memory_fixture(DATASET)

    assert report["passed"] is True
    assert report["mismatches"] == []
    assert set(report["metrics"]) == {
        "candidate_precision",
        "sensitive_rejection_rate",
        "conflict_rate",
        "confirmation_rate",
    }
    assert report["metrics"]["sensitive_rejection_rate"] == 1.0
    assert report["metrics"]["conflict_rate"] > 0
    assert report["metrics"]["confirmation_rate"] > 0
