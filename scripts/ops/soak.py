from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import time

import httpx


@dataclass(frozen=True)
class ProbeResult:
    passed: bool
    latency_ms: float
    error_code: str | None = None


@dataclass(frozen=True)
class SoakReport:
    status: str
    started_at: str
    ended_at: str
    configured_duration_seconds: float
    checks: int
    failures: int
    max_latency_ms: float
    error_codes: dict[str, int]


def run_soak(
    *,
    duration_seconds: float,
    interval_seconds: float,
    probe: Callable[[], ProbeResult],
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> SoakReport:
    if not 10 <= duration_seconds <= 86_400:
        raise ValueError("duration_seconds must be between 10 and 86400")
    if not 1 <= interval_seconds <= 300:
        raise ValueError("interval_seconds must be between 1 and 300")
    started_at = datetime.now(UTC)
    deadline = monotonic() + duration_seconds
    results: list[ProbeResult] = []
    while True:
        results.append(probe())
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleeper(min(interval_seconds, remaining))
    errors: dict[str, int] = {}
    for result in results:
        if result.error_code:
            errors[result.error_code] = errors.get(result.error_code, 0) + 1
    failures = sum(not result.passed for result in results)
    return SoakReport(
        status="passed" if failures == 0 else "failed",
        started_at=started_at.isoformat(),
        ended_at=datetime.now(UTC).isoformat(),
        configured_duration_seconds=duration_seconds,
        checks=len(results),
        failures=failures,
        max_latency_ms=max(result.latency_ms for result in results),
        error_codes=errors,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a bounded local API soak probe")
    parser.add_argument("--duration-seconds", type=float, default=3600)
    parser.add_argument("--interval-seconds", type=float, default=10)
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get("LOCAL_API_TOKEN", "").strip()
    headers = {"authorization": f"Bearer {token}"} if token else {}
    client = httpx.Client(base_url=args.api_base_url.rstrip("/"), headers=headers, timeout=5)

    def probe() -> ProbeResult:
        started = time.perf_counter()
        try:
            response = client.get("/health")
            passed = response.status_code == 200
            error_code = None if passed else f"http_{response.status_code}"
        except httpx.HTTPError:
            passed = False
            error_code = "connection_failed"
        latency = (time.perf_counter() - started) * 1000
        return ProbeResult(passed=passed, latency_ms=latency, error_code=error_code)

    try:
        report = run_soak(
            duration_seconds=args.duration_seconds,
            interval_seconds=args.interval_seconds,
            probe=probe,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 2
    finally:
        client.close()
    print(json.dumps(asdict(report), ensure_ascii=False))
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
