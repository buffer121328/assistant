from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ops.provider_smoke import REQUIRED_FIELDS, run_provider_smoke


@pytest.mark.asyncio
async def test_provider_smoke_reports_unconfigured_as_skipped_not_passed() -> None:
    report = await run_provider_smoke({})
    assert report["status"] == "incomplete"
    assert set(report["providers"]) == set(REQUIRED_FIELDS)
    assert all(item["status"] == "skipped" for item in report["providers"].values())


@pytest.mark.asyncio
async def test_provider_smoke_runs_only_fully_configured_provider() -> None:
    calls: list[str] = []

    async def probe(values: Mapping[str, str]) -> None:
        calls.append(values["SMOKE_BROWSER_URL"])

    async def unused(values: Mapping[str, str]) -> None:
        raise AssertionError(values)

    probes = {name: unused for name in REQUIRED_FIELDS}
    probes["browser"] = probe
    report = await run_provider_smoke(
        {"SMOKE_BROWSER_URL": "https://example.invalid"},
        probes=probes,
    )
    assert calls == ["https://example.invalid"]
    assert report["providers"]["browser"]["status"] == "passed"
    assert report["status"] == "incomplete"


@pytest.mark.asyncio
async def test_provider_smoke_redacts_probe_exception() -> None:
    async def fail(values: Mapping[str, str]) -> None:
        raise RuntimeError(f"private: {values['SMOKE_BROWSER_URL']}")

    probes = {name: fail for name in REQUIRED_FIELDS}
    report = await run_provider_smoke(
        {"SMOKE_BROWSER_URL": "https://private.example.invalid/token"},
        probes=probes,
    )
    assert report["status"] == "failed"
    assert report["providers"]["browser"]["code"] == "browser_smoke_failed"
    assert "private.example" not in str(report)


def test_provider_smoke_module_runs_from_repository_root_without_pytest_pythonpath() -> None:
    environment = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "")}
    result = subprocess.run(
        [sys.executable, "-m", "scripts.ops.provider_smoke"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["status"] == "incomplete"
    assert all(item["status"] == "skipped" for item in report["providers"].values())
