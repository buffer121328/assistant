from pathlib import Path
from evaluation.memory_retrieval import evaluate_memory_retrieval_fixture

DATASET = Path(__file__).parent / "datasets/memory_retrieval_v6_04.json"


def test_v6_retrieval_fixture_reports_release_dimensions() -> None:
    report = evaluate_memory_retrieval_fixture(DATASET)
    assert report["passed"] is True
    assert report["metrics"]["accuracy"] == 1.0
    assert report["metrics"]["stale_use_rate"] == 0.0
    assert report["metrics"]["average_tokens"] > 0
    assert report["metrics"]["p95_latency_ms"] > 0
