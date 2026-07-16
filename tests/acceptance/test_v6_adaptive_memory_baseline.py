from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from packages.evaluation import DatasetSecurityError, EvaluationDataError
from packages.evaluation.memory_baseline import load_memory_baseline_fixture


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/evals/datasets/adaptive_memory_v6_00.json"
SCRIPT = ROOT / "scripts/run_memory_baseline.py"


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_01_loader_rejects_taxonomy_drift(tmp_path: Path) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["taxonomy"]["cognitive_types"].remove("reflection")
    changed = tmp_path / "taxonomy-drift.json"
    write_json(changed, payload)

    with pytest.raises(EvaluationDataError, match="cognitive_types"):
        load_memory_baseline_fixture(changed)


def test_02_loader_rejects_unsafe_content_without_echoing_it(tmp_path: Path) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    unsafe = "Authorization: Bearer test-placeholder-not-a-secret"
    payload["forbidden_samples"][0]["placeholder"] = unsafe
    changed = tmp_path / "unsafe.json"
    write_json(changed, payload)

    with pytest.raises(DatasetSecurityError) as exc_info:
        load_memory_baseline_fixture(changed)

    assert unsafe not in str(exc_info.value)
    assert "synthetic-authorization-header" in str(exc_info.value)


def test_03_cli_is_deterministic_and_keeps_known_failures_visible() -> None:
    first = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert first.returncode == second.returncode == 1
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["valid"] is True
    assert payload["known_failures"]
    assert "raw_content" not in first.stdout


def test_04_cli_returns_data_error_without_leaking_fixture_content(
    tmp_path: Path,
) -> None:
    unsafe = "Cookie: session=test-placeholder-not-a-secret"
    changed = tmp_path / "unsafe.json"
    write_json(changed, {"version": "v6-00", "unsafe": unsafe})

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(changed)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert unsafe not in result.stdout


def test_05_readme_describes_baseline_without_claiming_v6_is_live() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "V6-00" in readme
    assert "run_memory_baseline.py" in readme
    assert "adaptive_memory_v6_00.json" in readme
    assert "尚未上线" in readme
