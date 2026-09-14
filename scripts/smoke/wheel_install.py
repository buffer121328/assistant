"""Build and import the backend wheel from an isolated environment.

Run from the repository root after dependencies are available:
    UV_CACHE_DIR=backend/.uv-cache uv run python scripts/smoke/wheel_install.py
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    """Build a wheel, install it in a fresh environment, and import runtime packages."""
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for the backend wheel smoke check")

    with tempfile.TemporaryDirectory(prefix="assistant-wheel-smoke-") as temporary:
        temporary_root = Path(temporary)
        distribution_dir = temporary_root / "dist"
        environment_dir = temporary_root / "venv"

        _run(
            [uv, "build", "--wheel", "--out-dir", str(distribution_dir)],
            cwd=BACKEND,
        )
        wheels = sorted(distribution_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("wheel build did not produce exactly one artifact")

        _run([uv, "venv", str(environment_dir)], cwd=temporary_root)
        interpreter = environment_dir / "bin" / "python"
        _run(
            [uv, "pip", "install", "--python", str(interpreter), str(wheels[0])],
            cwd=temporary_root,
        )
        _run(
            [
                str(interpreter),
                "-I",
                "-c",
                "import app.main; import features.catalog",
            ],
            cwd=temporary_root,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
