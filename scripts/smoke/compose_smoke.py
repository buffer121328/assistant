from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import subprocess
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


PROJECT = "assistant-v5-integration"
API_BASE_URL = "http://127.0.0.1:18000"
INTEGRATION_API_TOKEN = "integration-only-token"
BASE = (
    "docker",
    "compose",
    "--env-file",
    "tests/integration/compose.env",
    "-f",
    "docker-compose.yml",
    "-f",
    "tests/integration/docker-compose.yml",
    "-p",
    PROJECT,
)
Run = Callable[..., subprocess.CompletedProcess[str]]


class ComposeSmokeError(RuntimeError):
    pass


def run_compose_smoke(*, run: Run = subprocess.run) -> dict[str, Any]:
    _require_files()
    checks: dict[str, str] = {}
    try:
        _command(run, "config", "--quiet")
        checks["compose_config"] = "passed"
        _command(run, "up", "-d", "--build", "--wait")
        checks["stack_health"] = "passed"
        _http_check(f"{API_BASE_URL}/health")
        checks["api_health"] = "passed"
        _http_check(
            f"{API_BASE_URL}/local/config",
            expected_status=401,
        )
        _http_check(
            f"{API_BASE_URL}/local/config",
            authorization=f"Bearer {INTEGRATION_API_TOKEN}",
        )
        checks["api_auth"] = "passed"
        _runtime_storage_checks(run)
        checks["runtime_storage"] = "passed"
        migration = _command(
            run,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "assistant",
            "-d",
            "assistant_integration",
            "-At",
            "-c",
            "SELECT version_num FROM alembic_version",
        )
        if not migration.stdout.strip():
            raise ComposeSmokeError("migration_head_missing")
        checks["migration"] = "passed"
        redis = _command(run, "exec", "-T", "redis", "redis-cli", "ping")
        if redis.stdout.strip() != "PONG":
            raise ComposeSmokeError("redis_ping_failed")
        checks["redis"] = "passed"
        _worker_ping(run)
        checks["worker"] = "passed"

        _command(run, "stop", "celery-worker")
        _command(run, "stop", "redis")
        _command(run, "up", "-d", "--wait", "redis")
        _command(run, "up", "-d", "--wait", "celery-worker")
        _worker_ping(run)
        checks["restart_recovery"] = "passed"
    except (OSError, subprocess.SubprocessError, ComposeSmokeError) as exc:
        checks.setdefault(_safe_code(exc), "failed")
        return {"status": "failed", "checks": checks}
    finally:
        try:
            _command(run, "down", "--volumes", "--remove-orphans")
        except (OSError, subprocess.SubprocessError, ComposeSmokeError):
            checks["cleanup"] = "failed"
    status = "passed" if all(value == "passed" for value in checks.values()) else "failed"
    return {"status": status, "checks": checks}


def _worker_ping(run: Run) -> None:
    result = _command(
        run,
        "exec",
        "-T",
        "celery-worker",
        "celery",
        "-A",
        "workers.worker:celery_app",
        "inspect",
        "ping",
        "--timeout",
        "10",
    )
    if "pong" not in result.stdout.lower():
        raise ComposeSmokeError("worker_ping_failed")


def _http_check(
    url: str,
    *,
    authorization: str | None = None,
    expected_status: int = 200,
) -> None:
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    request = urllib_request.Request(url, headers=headers)
    try:
        with urllib_request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib_error.HTTPError as exc:
        status = exc.code
    except OSError as exc:
        raise ComposeSmokeError("api_request_failed") from exc
    if status != expected_status:
        raise ComposeSmokeError("api_unexpected_status")


def _runtime_storage_checks(run: Run) -> None:
    probes = (
        ("assistant-api", "/app/data/artifacts"),
        ("assistant-api", "/app/data/workspace/sessions"),
        ("celery-beat", "/app/run"),
    )
    for service, path in probes:
        _command(
            run,
            "exec",
            "-T",
            service,
            "sh",
            "-c",
            f"touch {path}/.compose-smoke && rm {path}/.compose-smoke",
        )


def _command(run: Run, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return run(
            [*BASE, *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.CalledProcessError as exc:
        action = args[0] if args else "unknown"
        raise ComposeSmokeError(f"compose_{action}_failed") from exc


def _safe_code(exc: BaseException) -> str:
    value = str(exc)
    if value and value.replace("_", "").isalnum():
        return value
    return "compose_smoke_failed"


def _require_files() -> None:
    for path in (Path("docker-compose.yml"), Path("tests/integration/compose.env")):
        if not path.is_file():
            raise ComposeSmokeError("compose_smoke_files_missing")


def main() -> int:
    report = run_compose_smoke()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
