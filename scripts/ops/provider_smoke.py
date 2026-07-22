from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys
from time import monotonic
from typing import Any

import httpx

API_ROOT = Path(__file__).resolve().parents[2] / "backend" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from integrations import CalDavProvider, ProviderError, SmtpProvider  # noqa: E402
from tools import (  # noqa: E402
    TavilyApiClient,
    TavilyConfig,
    TavilySearchRequest,
)


@dataclass(frozen=True)
class SmokeResult:
    status: str
    code: str
    latency_ms: float = 0.0


Probe = Callable[[Mapping[str, str]], Awaitable[None]]
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "langbot": ("SMOKE_LANGBOT_HEALTH_URL", "SMOKE_LANGBOT_API_KEY"),
    "models": ("SMOKE_MODEL_GATEWAY_HEALTH_URL", "SMOKE_MODEL_GATEWAY_API_KEY"),
    "tavily": ("SMOKE_TAVILY_BASE_URL", "SMOKE_TAVILY_API_KEY"),
    "smtp": (
        "SMOKE_SMTP_HOST",
        "SMOKE_SMTP_PORT",
        "SMOKE_SMTP_USERNAME",
        "SMOKE_SMTP_PASSWORD",
        "SMOKE_SMTP_FROM",
        "SMOKE_SMTP_RECIPIENT",
        "SMOKE_SMTP_SECURITY",
    ),
    "caldav": (
        "SMOKE_CALDAV_URL",
        "SMOKE_CALDAV_USERNAME",
        "SMOKE_CALDAV_PASSWORD",
    ),
    "browser": ("SMOKE_BROWSER_URL",),
}


async def run_provider_smoke(
    environ: Mapping[str, str] | None = None,
    *,
    probes: Mapping[str, Probe] | None = None,
) -> dict[str, Any]:
    values = environ or os.environ
    available = probes or _default_probes()
    results: dict[str, SmokeResult] = {}
    for provider, fields in REQUIRED_FIELDS.items():
        configured = [bool(values.get(field, "").strip()) for field in fields]
        if not any(configured):
            results[provider] = SmokeResult("skipped", "unconfigured")
            continue
        if not all(configured):
            results[provider] = SmokeResult("skipped", "configuration_incomplete")
            continue
        started = monotonic()
        try:
            await available[provider](values)
        except ProviderError as exc:
            results[provider] = SmokeResult(
                "failed", exc.code, _latency(started)
            )
        except (httpx.HTTPError, TimeoutError, OSError):
            results[provider] = SmokeResult(
                "failed", f"{provider}_connection_failed", _latency(started)
            )
        except Exception:
            results[provider] = SmokeResult(
                "failed", f"{provider}_smoke_failed", _latency(started)
            )
        else:
            results[provider] = SmokeResult("passed", "ok", _latency(started))
    statuses = {result.status for result in results.values()}
    overall = "failed" if "failed" in statuses else "passed" if statuses == {"passed"} else "incomplete"
    return {
        "status": overall,
        "providers": {name: asdict(result) for name, result in results.items()},
    }


def _default_probes() -> dict[str, Probe]:
    return {
        "langbot": _probe_langbot,
        "models": _probe_models,
        "tavily": _probe_tavily,
        "smtp": _probe_smtp,
        "caldav": _probe_caldav,
        "browser": _probe_browser,
    }


async def _probe_langbot(values: Mapping[str, str]) -> None:
    await _http_health(values["SMOKE_LANGBOT_HEALTH_URL"], values["SMOKE_LANGBOT_API_KEY"])


async def _probe_models(values: Mapping[str, str]) -> None:
    await _http_health(
        values["SMOKE_MODEL_GATEWAY_HEALTH_URL"],
        values["SMOKE_MODEL_GATEWAY_API_KEY"],
    )


async def _http_health(url: str, token: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()


async def _probe_tavily(values: Mapping[str, str]) -> None:
    client = TavilyApiClient(
        TavilyConfig(
            api_key=values["SMOKE_TAVILY_API_KEY"],
            base_url=values["SMOKE_TAVILY_BASE_URL"],
            timeout_seconds=10.0,
            max_results=1,
        )
    )
    await client.search(
        TavilySearchRequest(
            task_id="provider-smoke",
            user_id="provider-smoke",
            query=values.get("SMOKE_TAVILY_QUERY", "OpenAI"),
            max_results=1,
        )
    )


async def _probe_smtp(values: Mapping[str, str]) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    await SmtpProvider().send(
        {
            "host": values["SMOKE_SMTP_HOST"],
            "port": values["SMOKE_SMTP_PORT"],
            "username": values["SMOKE_SMTP_USERNAME"],
            "password": values["SMOKE_SMTP_PASSWORD"],
            "from_address": values["SMOKE_SMTP_FROM"],
            "security": values["SMOKE_SMTP_SECURITY"],
            "timeout": "15",
        },
        recipients=(values["SMOKE_SMTP_RECIPIENT"],),
        subject="Personal Agent SMTP smoke",
        body=f"Explicit SMTP smoke at {now}",
    )


async def _probe_caldav(values: Mapping[str, str]) -> None:
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=365)
    end = start + timedelta(minutes=5)
    await CalDavProvider().create_event(
        {
            "url": values["SMOKE_CALDAV_URL"],
            "username": values["SMOKE_CALDAV_USERNAME"],
            "password": values["SMOKE_CALDAV_PASSWORD"],
            "calendar_url": values.get("SMOKE_CALDAV_CALENDAR_URL", ""),
        },
        title="Personal Agent CalDAV smoke",
        start=start.strftime("%Y%m%dT%H%M%SZ"),
        end=end.strftime("%Y%m%dT%H%M%SZ"),
        description="Explicit provider smoke event",
        idempotency_key=values.get("SMOKE_CALDAV_IDEMPOTENCY_KEY", "provider-smoke-v1"),
    )


async def _probe_browser(values: Mapping[str, str]) -> None:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as exc:
        raise ProviderError("browser_smoke_dependency_missing") from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            response = await page.goto(values["SMOKE_BROWSER_URL"], wait_until="domcontentloaded", timeout=20_000)
            if response is None or not response.ok:
                raise RuntimeError("browser_response_failed")
        finally:
            await browser.close()


def _latency(started: float) -> float:
    return round((monotonic() - started) * 1000, 2)


def main() -> int:
    report = asyncio.run(run_provider_smoke())
    print(json.dumps(report, sort_keys=True))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
