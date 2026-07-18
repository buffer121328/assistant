from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "tests/evals/datasets/core_commands.json"
DEFAULT_BASELINE = ROOT / "tests/evals/baselines/v2-05.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the deterministic local evaluation regression suite."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--candidate", type=Path)
    return parser.parse_args()


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from evaluation import EvaluationDataError, evaluate_dataset

    args = parse_args()
    try:
        report = evaluate_dataset(args.dataset, args.baseline, args.candidate)
    except EvaluationDataError as exc:
        payload = {
            "passed": False,
            "error": str(exc),
            "results": [],
            "regressions": [],
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 2

    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
