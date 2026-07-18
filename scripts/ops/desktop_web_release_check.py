from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DESKTOP_ROOT = ROOT / "apps" / "desktop-web"

REQUIRED_EXCLUDES = {
    "!**/.venv/**",
    "!**/.git/**",
    "!**/.mypy_cache/**",
    "!**/.pytest_cache/**",
    "!**/.ruff_cache/**",
    "!**/tests/**",
    "!**/node_modules/.cache/**",
}


def main() -> None:
    package = json.loads((DESKTOP_ROOT / "package.json").read_text())
    lockfile = DESKTOP_ROOT / "package-lock.json"
    builder = json.loads((DESKTOP_ROOT / "electron-builder.json").read_text())
    release = (DESKTOP_ROOT / "RELEASE.md").read_text()

    if not lockfile.is_file():
        raise SystemExit("desktop_web_package_lock_missing")
    scripts = package.get("scripts", {})
    if "dist" not in scripts or "dist:dir" not in scripts:
        raise SystemExit("desktop_web_release_scripts_missing")
    if "electron-builder" not in package.get("devDependencies", {}):
        raise SystemExit("electron_builder_dependency_missing")
    files = set(builder.get("files", []))
    missing = sorted(REQUIRED_EXCLUDES - files)
    if missing:
        raise SystemExit(f"desktop_web_release_excludes_missing: {missing}")
    if builder.get("publish") is not None:
        raise SystemExit("desktop_web_publish_must_be_disabled_until_signing_is_verified")
    if "external installed mode" not in release:
        raise SystemExit("desktop_web_distribution_mode_not_documented")
    if "not measured" not in release:
        raise SystemExit("desktop_web_metrics_status_not_documented")
    print("desktop_web_release_check_ok")


if __name__ == "__main__":
    main()
