from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

from evaluation import evaluate_governance_fixture, evaluate_rag_retrieval_fixture


ROOT = Path(__file__).parents[2]
RAG_DATASET = Path(__file__).parent / "datasets/rag_governance_v12_06.json"
AGENT_DATASET = Path(__file__).parent / "datasets/agent_governance_v12_07.json"
SCRIPT = ROOT / "scripts/run_v12_governance_gate.py"
RETRIEVAL_DATASET = Path(__file__).parent / "datasets/rag_retrieval_v12_06.json"


def test_v12_governance_fixtures_cover_rag_trajectory_and_security() -> None:
    rag = evaluate_governance_fixture(RAG_DATASET, expected_version="v12-06")
    agent = evaluate_governance_fixture(AGENT_DATASET, expected_version="v12-07")

    assert rag["passed"] is True
    assert {"citation", "abstention", "deletion", "injection"} <= set(rag["categories"])
    assert agent["passed"] is True
    assert {"trace", "trajectory", "quality", "security"} <= set(agent["categories"])


def test_v12_real_rag_retrieval_fixture_runs_the_knowledge_service() -> None:
    report = asyncio.run(evaluate_rag_retrieval_fixture(RETRIEVAL_DATASET))

    assert report["passed"] is True
    assert report["case_count"] == 5
    assert report["metrics"] == {
        "mean_recall_at_k": 1.0,
        "abstention_accuracy": 1.0,
        "instruction_risk_accuracy": 1.0,
    }
    assert report["failed_cases"] == []


def test_v12_governance_cli_saves_a_local_report(tmp_path: Path) -> None:
    report_path = tmp_path / "v12-governance.json"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--report", str(report_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["suite"] == "v12-local-governance"
    assert payload["passed"] is True
    assert payload["rag_retrieval"]["metrics"]["mean_recall_at_k"] == 1.0
    assert json.loads(completed.stdout)["passed"] is True
