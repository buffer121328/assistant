from __future__ import annotations

from pathlib import Path

from tools.artifacts import ArtifactStore, ProductivityTools
from tools.personal import build_personal_tool_descriptors, build_personal_tool_specs
from tools.sandbox import DockerSandboxConfig, build_sandbox_runner
from infrastructure.config import Settings


def test_default_local_config_uses_no_sandbox_provider_and_no_shell_exec(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    runner = build_sandbox_runner(
        provider=settings.effective_sandbox_provider,
        docker_config=DockerSandboxConfig(
            enabled=settings.effective_shell_exec_enabled,
            image=settings.effective_sandbox_docker_image,
            allowed_images=settings.effective_sandbox_docker_allowed_images_tuple,
            timeout_seconds=settings.sandbox_timeout_seconds,
        ),
        workspace_root=tmp_path,
    )
    descriptors = build_personal_tool_descriptors(
        browser_available=False,
        sandbox_available=runner.available,
    )

    assert settings.effective_sandbox_provider == "none"
    assert settings.effective_shell_exec_enabled is False
    assert runner.available is False
    assert {item.name: item for item in descriptors}["shell.exec"].enabled is False


def test_docker_provider_does_not_expose_shell_without_shell_exec_flag(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        sandbox_provider="docker",
        shell_exec_enabled=False,
        sandbox_docker_image="python:3.12-alpine",
        sandbox_docker_allowed_images="python:3.12-alpine",
    )

    runner = build_sandbox_runner(
        provider=settings.effective_sandbox_provider,
        docker_config=DockerSandboxConfig(
            enabled=settings.effective_shell_exec_enabled,
            image=settings.effective_sandbox_docker_image,
            allowed_images=settings.effective_sandbox_docker_allowed_images_tuple,
        ),
        workspace_root=tmp_path,
    )
    specs = build_personal_tool_specs(
        productivity=ProductivityTools(ArtifactStore(tmp_path / "artifacts")),
        sandbox=runner,
    )

    assert settings.effective_sandbox_provider == "docker"
    assert settings.effective_shell_exec_enabled is False
    assert "shell.exec" not in {item.name for item in specs}


def test_legacy_sandbox_enabled_settings_resolve_to_docker_provider() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        sandbox_enabled=True,
        sandbox_image="python:3.12-alpine",
        sandbox_allowed_images="python:3.12-alpine,ubuntu:24.04",
    )

    assert settings.effective_sandbox_provider == "docker"
    assert settings.effective_shell_exec_enabled is True
    assert settings.effective_sandbox_docker_image == "python:3.12-alpine"
    assert settings.effective_sandbox_docker_allowed_images_tuple == (
        "python:3.12-alpine",
        "ubuntu:24.04",
    )
