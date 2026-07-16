from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "tests/evals/datasets/adaptive_memory_v6_00.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the deterministic V6-00 adaptive-memory baseline."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    return parser.parse_args()


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from packages.evaluation import EvaluationDataError
    from packages.evaluation.memory_baseline import evaluate_memory_baseline

    args = parse_args()
    try:
        report = evaluate_memory_baseline(args.dataset)
    except EvaluationDataError as exc:
        payload = {
            "valid": False,
            "error": str(exc),
            "known_failures": [],
            "metrics": {},
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 2

    payload = report.to_dict()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 1 if report.known_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
