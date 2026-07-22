from __future__ import annotations

from pathlib import Path

from .base import SandboxRunner
from .disabled import DisabledSandboxRunner
from .docker import DockerSandboxRunner
from .types import DockerSandboxConfig, SandboxProvider, SandboxResult


def build_sandbox_runner(
    *,
    provider: SandboxProvider,
    docker_config: DockerSandboxConfig,
    workspace_root: Path,
) -> SandboxRunner:
    """构建 sandbox runner。

    Args:
        provider: provider 参数。
        docker_config: docker_config 参数。
        workspace_root: workspace_root 参数。
    """
    if provider == "docker":
        return DockerSandboxRunner(config=docker_config, workspace_root=workspace_root)
    return DisabledSandboxRunner()


__all__ = [
    "DisabledSandboxRunner",
    "DockerSandboxConfig",
    "DockerSandboxRunner",
    "SandboxProvider",
    "SandboxResult",
    "SandboxRunner",
    "build_sandbox_runner",
]
