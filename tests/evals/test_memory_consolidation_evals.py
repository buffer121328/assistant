import json
from pathlib import Path

DATASET = Path(__file__).parent / "datasets/memory_consolidation_v6_05.json"


def test_v6_consolidation_fixture_preserves_history_and_idempotency() -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = payload["cases"]
    assert payload["version"] == "v6-05"
    assert all(case["correct"] for case in cases)
    assert all(case["history_preserved"] for case in cases)
    assert all(case["idempotent"] for case in cases)
