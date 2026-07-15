from __future__ import annotations

from pathlib import Path
import subprocess

from scripts.ops.compose_smoke import PROJECT, run_compose_smoke


ROOT = Path(__file__).resolve().parents[2]


def test_compose_smoke_uses_isolated_project_and_cleans_up() -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        stdout = ""
        if "SELECT version_num FROM alembic_version" in command:
            stdout = "202607150001\n"
        elif command[-2:] == ["redis-cli", "ping"]:
            stdout = "PONG\n"
        elif "inspect" in command:
            stdout = "worker: pong\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    report = run_compose_smoke(run=fake_run)
    assert report["status"] == "passed"
    assert all(PROJECT in command for command in commands)
    assert commands[-1][-3:] == ["down", "--volumes", "--remove-orphans"]
    assert any(command[-2:] == ["stop", "redis"] for command in commands)


def test_compose_smoke_returns_safe_failure_and_still_cleans_up() -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        if command[-2:] == ["config", "--quiet"]:
            raise subprocess.CalledProcessError(1, command, stderr="private provider response")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    report = run_compose_smoke(run=fake_run)
    assert report == {"status": "failed", "checks": {"compose_config_failed": "failed"}}
    assert commands[-1][-3:] == ["down", "--volumes", "--remove-orphans"]
    assert "private provider response" not in str(report)


def test_postgres_healthcheck_uses_configured_database_identity() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'pg_isready -U "$${POSTGRES_USER}" -d "$${POSTGRES_DB}"' in compose
