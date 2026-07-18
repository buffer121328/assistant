from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "tests/evals/datasets/memory_release_v6_07.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the deterministic V6-07 adaptive-memory release gate."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--manual-evidence", type=Path)
    return parser.parse_args()


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from evaluation import EvaluationDataError
    from evaluation.memory_release import evaluate_memory_release_fixture

    try:
        args = parse_args()
        report = evaluate_memory_release_fixture(
            args.dataset, manual_evidence_path=args.manual_evidence
        )
    except EvaluationDataError as exc:
        print(
            json.dumps(
                {"valid": False, "error": str(exc), "gate_reasons": []},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
