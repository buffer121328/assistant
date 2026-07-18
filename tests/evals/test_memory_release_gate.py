from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from evaluation import EvaluationDataError
from evaluation.memory_release import evaluate_memory_release_fixture


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evals/datasets/memory_release_v6_07.json"
SCRIPT = ROOT / "scripts/run_memory_release_gate.py"


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_release_report_is_deterministic_safe_and_manual_evidence_is_pending() -> None:
    first = evaluate_memory_release_fixture(DATASET)
    second = evaluate_memory_release_fixture(DATASET)

    assert first == second
    assert first["version"] == "v6-07"
    assert first["automated_passed"] is True
    assert first["passed"] is False
    assert first["gate_reasons"] == ["manual_evidence_pending"]
    assert first["metrics"]["accuracy"] == 1.0
    assert first["metrics"]["evidence_precision"] == 1.0
    assert first["metrics"]["evidence_recall"] == 1.0
    assert first["metrics"]["cross_user_leak_count"] == 0
    assert first["metrics"]["forbidden_write_count"] == 0
    assert "content" not in json.dumps(first, ensure_ascii=False).lower()


def test_release_gate_hard_fails_leak_forbidden_stale_and_quality_thresholds(
    tmp_path: Path,
) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    case = payload["cases"][0]
    case.update(
        {
            "accurate": False,
            "stale_used": True,
            "contradiction_injected": True,
            "cross_user_leak": True,
            "forbidden_write": True,
            "latency_ms": 999.0,
            "context_tokens": 999,
        }
    )
    path = tmp_path / "failed.json"
    write_json(path, payload)

    report = evaluate_memory_release_fixture(path)

    assert report["automated_passed"] is False
    assert set(report["gate_reasons"]) >= {
        "cross_user_leak_nonzero",
        "forbidden_write_nonzero",
        "stale_use_rate_exceeded",
        "contradiction_rate_exceeded",
        "accuracy_below_minimum",
        "p95_latency_exceeded",
        "average_context_tokens_exceeded",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(version="wrong"),
        lambda payload: payload.update(cases=[]),
        lambda payload: payload["cases"][0].update(cross_user_leak="no"),
        lambda payload: payload["thresholds"].pop("max_stale_use_rate"),
        lambda payload: payload["manual_evidence"].update(provided=["unknown"]),
    ],
)
def test_release_fixture_rejects_malformed_input_without_echoing_values(
    tmp_path: Path, mutation: object
) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    mutation(payload)  # type: ignore[operator]
    path = tmp_path / "invalid.json"
    write_json(path, payload)

    with pytest.raises(EvaluationDataError, match="release fixture"):
        evaluate_memory_release_fixture(path)


def test_release_cli_uses_status_aligned_exit_codes(tmp_path: Path) -> None:
    pending = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    pending_payload = json.loads(pending.stdout)
    assert pending.returncode == 1
    assert pending_payload["automated_passed"] is True
    assert pending_payload["gate_reasons"] == ["manual_evidence_pending"]

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{}", encoding="utf-8")
    invalid = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(invalid_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    invalid_payload = json.loads(invalid.stdout)
    assert invalid.returncode == 2
    assert invalid_payload["valid"] is False


def test_private_manual_evidence_manifest_can_complete_release(tmp_path: Path) -> None:
    evidence = {
        "format": "v6-local-trial-evidence-v1",
        "evidence": [
            {
                "type": evidence_type,
                "evidence_id": f"local-{index}",
                "observed_at": "2026-07-16T12:00:00+08:00",
            }
            for index, evidence_type in enumerate(
                (
                    "knowledge_update",
                    "correction",
                    "forgetting",
                    "long_session_compaction",
                ),
                start=1,
            )
        ],
    }
    path = tmp_path / "local-evidence.json"
    write_json(path, evidence)

    report = evaluate_memory_release_fixture(DATASET, manual_evidence_path=path)

    assert report["passed"] is True
    assert report["manual_evidence_complete"] is True


def test_manual_evidence_manifest_rejects_raw_content_fields(tmp_path: Path) -> None:
    path = tmp_path / "unsafe-evidence.json"
    write_json(
        path,
        {
            "format": "v6-local-trial-evidence-v1",
            "evidence": [
                {
                    "type": "correction",
                    "evidence_id": "local-1",
                    "observed_at": "2026-07-16T12:00:00+08:00",
                    "content": "must not be accepted",
                }
            ],
        },
    )

    with pytest.raises(EvaluationDataError, match="manifest entry"):
        evaluate_memory_release_fixture(DATASET, manual_evidence_path=path)
