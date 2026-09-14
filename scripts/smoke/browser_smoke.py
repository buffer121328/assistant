from __future__ import annotations

import asyncio
import json

from playwright.async_api import Error, async_playwright


async def run() -> int:
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                )
                try:
                    page = await context.new_page()
                    await page.set_content("<title>browser-smoke</title><p>ready</p>")
                    if await page.title() != "browser-smoke":
                        raise RuntimeError("unexpected_title")
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Error as exc:
        message = str(exc).lower()
        if "executable doesn't exist" in message or "browser was not found" in message:
            print(json.dumps({"check": "playwright_chromium", "status": "skipped", "reason": "binary_missing"}))
            return 0
        print(json.dumps({"check": "playwright_chromium", "status": "failed", "reason": "launch_failed"}))
        return 1
    except Exception:
        print(json.dumps({"check": "playwright_chromium", "status": "failed", "reason": "smoke_failed"}))
        return 1
    print(json.dumps({"check": "playwright_chromium", "status": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
