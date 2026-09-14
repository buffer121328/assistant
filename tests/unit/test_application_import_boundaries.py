from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _run_fresh_backend_import(source: str) -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT / "backend",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_artifact_lifecycle_is_directly_importable() -> None:
    """Artifact lifecycle must not depend on session-context package import order."""
    _run_fresh_backend_import(
        "import application.artifact_lifecycle as artifacts\n"
        "assert artifacts.ArtifactLifecycleError.__name__ == 'ArtifactLifecycleError'\n"
        "assert artifacts.ArtifactLifecycleService.__name__ == 'ArtifactLifecycleService'\n"
    )


def test_session_context_public_exports_remain_available() -> None:
    """The compatibility package must resolve every documented re-export."""
    _run_fresh_backend_import(
        "import application.session_context as session_context\n"
        "for name in session_context.__all__:\n"
        "    assert getattr(session_context, name) is not None, name\n"
    )
