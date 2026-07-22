from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .loader import EvaluationDataError


def evaluate_memory_retrieval_fixture(path: Path) -> dict[str, Any]:
    """处理 evaluate memory retrieval fixture。

    Args:
        path: path 参数。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationDataError("unable to load retrieval fixture") from exc
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if payload.get("version") != "v6-04" or not isinstance(cases, list) or not cases:
        raise EvaluationDataError("retrieval fixture is invalid")
    normalized = []
    for case in cases:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("id"), str)
            or not isinstance(case.get("correct"), bool)
            or not isinstance(case.get("tokens"), int)
            or not isinstance(case.get("latency_ms"), (int, float))
            or not isinstance(case.get("stale_used"), bool)
        ):
            raise EvaluationDataError("retrieval fixture case is invalid")
        normalized.append(case)
    latencies = sorted(float(case["latency_ms"]) for case in normalized)
    p95 = latencies[min(len(latencies) - 1, max(0, int(len(latencies) * 0.95) - 1))]
    return {
        "version": "v6-04",
        "passed": all(case["correct"] for case in normalized)
        and not any(case["stale_used"] for case in normalized),
        "metrics": {
            "accuracy": sum(case["correct"] for case in normalized) / len(normalized),
            "average_tokens": sum(case["tokens"] for case in normalized)
            / len(normalized),
            "p95_latency_ms": p95,
            "stale_use_rate": sum(case["stale_used"] for case in normalized)
            / len(normalized),
        },
        "case_ids": [case["id"] for case in normalized],
    }
