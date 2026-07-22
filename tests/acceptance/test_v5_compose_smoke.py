from __future__ import annotations

from email.message import Message
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib import request as urllib_request
from urllib.request import Request

from pytest import MonkeyPatch
import yaml  # type: ignore[import-untyped]

from scripts.ops.compose_smoke import PROJECT, run_compose_smoke


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_SERVICE_ENVIRONMENT_KEYS = {
    "SEARCH_PROVIDER_ORDER",
    "BRAVE_SEARCH_API_KEY",
    "BRAVE_SEARCH_BASE_URL",
    "DUCKDUCKGO_SEARCH_ENABLED",
    "DUCKDUCKGO_SEARCH_BASE_URL",
    "SEARCH_FALLBACK_ON_EMPTY",
    "SEARCH_PROVIDER_TIMEOUT_SECONDS",
    "WORKSPACE_CONTEXT_ROOT",
    "WORKSPACE_CONTEXT_ENABLED",
    "WORKSPACE_CONTEXT_DENY_GLOBS",
    "WORKSPACE_CONTEXT_MAX_FILE_BYTES",
    "WORKSPACE_CONTEXT_MAX_RESULTS",
    "READONLY_SHELL_ENABLED",
    "READONLY_SHELL_TIMEOUT_SECONDS",
    "READONLY_SHELL_MAX_OUTPUT_CHARS",
    "SANDBOX_PROVIDER",
    "SHELL_EXEC_ENABLED",
    "SANDBOX_DOCKER_IMAGE",
    "SANDBOX_DOCKER_ALLOWED_IMAGES",
}
LEGACY_SANDBOX_ENVIRONMENT_KEYS = {
    "SANDBOX_ENABLED",
    "SANDBOX_IMAGE",
    "SANDBOX_ALLOWED_IMAGES",
}


class FakeHttpResponse:
    status = 200

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"status":"ok"}'


def install_http_fake(monkeypatch: MonkeyPatch) -> list[Request | str]:
    requests: list[Request | str] = []

    def fake_urlopen(request: Request | str, **kwargs: object) -> FakeHttpResponse:
        del kwargs
        requests.append(request)
        url = request.full_url if isinstance(request, Request) else request
        authorization = request.get_header("Authorization") if isinstance(request, Request) else None
        if url.endswith("/local/config") and authorization is None:
            raise HTTPError(url, 401, "Unauthorized", hdrs=Message(), fp=None)
        return FakeHttpResponse()

    monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)
    return requests


def request_url(request: Request | str) -> str:
    return request.full_url if isinstance(request, Request) else request


def request_authorization(request: Request | str) -> str | None:
    return request.get_header("Authorization") if isinstance(request, Request) else None


def test_compose_smoke_uses_isolated_project_and_cleans_up(monkeypatch: MonkeyPatch) -> None:
    commands: list[list[str]] = []
    install_http_fake(monkeypatch)

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
    compose_commands = [command for command in commands if command[:2] == ["docker", "compose"]]
    assert all(PROJECT in command for command in compose_commands)
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


def test_dockerfile_copies_backend_before_frozen_runtime_dependency_sync() -> None:
    instructions = [
        line.strip()
        for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    backend_copy = next(
        index
        for index, instruction in enumerate(instructions)
        if instruction.startswith("COPY ") and "backend" in instruction.split()
    )
    frozen_sync = next(
        index
        for index, instruction in enumerate(instructions)
        if instruction.startswith("RUN ")
        and "uv sync" in instruction
        and "--frozen" in instruction
        and "--no-dev" in instruction
    )

    assert backend_copy < frozen_sync


def test_compose_runtime_init_prepares_all_application_writable_volumes() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    def writable_named_volumes(service_name: str) -> set[str]:
        volumes: set[str] = set()
        for mount in services[service_name].get("volumes", []):
            if isinstance(mount, str):
                source, _, mode = mount.partition(":")
                if source and mode and not mode.endswith(":ro"):
                    volumes.add(source)
            elif mount.get("type") == "volume" and not mount.get("read_only", False):
                volumes.add(mount["source"])
        return volumes

    application_volumes = set().union(
        *(writable_named_volumes(name) for name in ("assistant-api", "celery-worker", "celery-beat"))
    )
    runtime_init = services["runtime-init"]
    initialized_volumes = {
        mount.partition(":")[0]
        if isinstance(mount, str)
        else mount["source"]
        for mount in runtime_init.get("volumes", [])
        if (isinstance(mount, str) and mount.partition(":")[0])
        or (isinstance(mount, dict) and mount.get("type") == "volume")
    }
    command = runtime_init.get("command", [])
    command_text = " ".join(command) if isinstance(command, list) else command

    assert application_volumes <= initialized_volumes
    assert runtime_init.get("user") in {"0", "0:0", "root"}
    assert "chown" in command_text
    assert "10001:10001" in command_text
    for service_name in ("assistant-api", "celery-worker", "celery-beat"):
        assert services[service_name]["depends_on"]["runtime-init"] == {
            "condition": "service_completed_successfully"
        }


def test_compose_smoke_checks_urllib_readiness_and_config_auth_boundary(
    monkeypatch: MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    requests = install_http_fake(monkeypatch)

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
    config_requests = [request for request in requests if request_url(request).endswith("/local/config")]

    assert report["status"] == "passed"
    assert not any(command[0] == "curl" for command in commands)
    assert any(request_url(request).endswith("/health") for request in requests)
    assert [request_authorization(request) for request in config_requests] == [
        None,
        "Bearer integration-only-token",
    ]


def test_compose_smoke_writes_and_removes_probes_in_application_runtime_mounts(
    monkeypatch: MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    install_http_fake(monkeypatch)

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
    runtime_probe_checks = [
        command
        for command in commands
        if command[:2] == ["docker", "compose"]
        and "exec" in command
        and any(service in command for service in ("assistant-api", "celery-worker", "celery-beat"))
        and "touch" in " ".join(command)
        and "rm" in " ".join(command)
    ]

    assert report["status"] == "passed"
    for mount_path in ("/app/data/artifacts", "/app/data/workspace/sessions", "/app/run"):
        assert any(mount_path in " ".join(command) for command in runtime_probe_checks)


def test_env_example_includes_postgres_settings_without_smoke_keys() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    keys = {
        line.partition("=")[0]
        for line in env_example.splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    assert {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"} <= keys
    assert not any(key.startswith("SMOKE_") for key in keys)


def test_compose_services_include_current_settings_environment_groups() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    for service_name in ("assistant-api", "celery-worker"):
        environment = compose["services"][service_name]["environment"]
        assert REQUIRED_SERVICE_ENVIRONMENT_KEYS <= set(environment)
        assert LEGACY_SANDBOX_ENVIRONMENT_KEYS <= set(environment)


def test_readme_compose_command_uses_assistant_api_service_name() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docker compose up --build assistant-api" in readme
    assert "docker compose up --build api " not in readme
