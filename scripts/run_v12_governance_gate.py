from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAG = ROOT / "tests/evals/datasets/rag_governance_v12_06.json"
DEFAULT_AGENT = ROOT / "tests/evals/datasets/agent_governance_v12_07.json"
DEFAULT_RETRIEVAL = ROOT / "tests/evals/datasets/rag_retrieval_v12_06.json"
DEFAULT_REPORT = ROOT / "var/evals/v12-governance-report.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local V12 governance evaluation gates.")
    parser.add_argument("--rag-dataset", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--agent-dataset", type=Path, default=DEFAULT_AGENT)
    parser.add_argument("--retrieval-dataset", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    from evaluation import (
        EvaluationDataError,
        evaluate_governance_fixture,
        evaluate_rag_retrieval_fixture,
    )

    try:
        rag = evaluate_governance_fixture(args.rag_dataset, expected_version="v12-06")
        agent = evaluate_governance_fixture(args.agent_dataset, expected_version="v12-07")
        retrieval = asyncio.run(evaluate_rag_retrieval_fixture(args.retrieval_dataset))
    except EvaluationDataError as exc:
        payload = {"passed": False, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    payload = {
        "suite": "v12-local-governance",
        "passed": bool(rag["passed"] and agent["passed"] and retrieval["passed"]),
        "rag": rag,
        "rag_retrieval": retrieval,
        "agent": agent,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
