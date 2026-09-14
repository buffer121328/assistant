from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from infrastructure.settings.config import load_settings
from application.desktop_capture.runners import build_screenshot_runner


async def main() -> int:
    settings = load_settings()
    if not settings.desktop_capture_enabled:
        print(
            "DESKTOP_CAPTURE_ENABLED=false; set it to true in local .env before smoke.",
            file=sys.stderr,
        )
        return 2
    runner = build_screenshot_runner(settings)
    data = await runner.capture_png(target=settings.desktop_capture_default_target)
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        print("capture did not return PNG bytes", file=sys.stderr)
        return 3
    output = Path("var/smoke/screenshot.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    print(f"wrote {output} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
