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
    parser.add_argument(
        "--langfuse",
        action="store_true",
        help="Also publish the evaluation cases as a Langfuse experiment when configured.",
    )
    parser.add_argument(
        "--langfuse-name",
        default="assistant.core_commands",
        help="Langfuse experiment name used with --langfuse.",
    )
    return parser.parse_args()


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from evaluation import (
        EvaluationDataError,
        evaluate_dataset,
        run_core_command_langfuse_experiment,
    )
    from infrastructure.config import load_settings

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

    if args.langfuse:
        settings = load_settings()
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            try:
                from langfuse import Langfuse

                client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    base_url=settings.langfuse_base_url,
                    environment=settings.app_env,
                )
                run_core_command_langfuse_experiment(
                    client=client,
                    dataset_path=args.dataset,
                    baseline_path=args.baseline,
                    candidate_path=args.candidate,
                    name=args.langfuse_name,
                    metadata={
                        "source": "scripts/run_evaluation.py",
                        "baseline_version": report.baseline_version,
                    },
                )
            except Exception as exc:
                print(f"Langfuse experiment skipped: {exc}", file=sys.stderr)
        else:
            print("Langfuse experiment skipped: missing credentials", file=sys.stderr)

    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
