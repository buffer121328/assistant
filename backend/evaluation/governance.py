from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .loader import EvaluationDataError


def evaluate_governance_fixture(path: Path, *, expected_version: str) -> dict[str, Any]:
    """处理 evaluate governance fixture。

    Args:
        path: path 参数。
        expected_version: expected_version 参数。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationDataError(f"unable to load governance fixture: {path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != expected_version:
        raise EvaluationDataError("governance fixture version is invalid")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationDataError("governance fixture cases are invalid")

    normalized: list[dict[str, Any]] = []
    for case in cases:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("id"), str)
            or not isinstance(case.get("category"), str)
            or not isinstance(case.get("passed"), bool)
            or not isinstance(case.get("evidence"), list)
            or not case["evidence"]
            or not all(isinstance(item, str) and item for item in case["evidence"])
        ):
            raise EvaluationDataError("governance fixture case is invalid")
        normalized.append(case)

    categories = sorted({case["category"] for case in normalized})
    return {
        "version": expected_version,
        "passed": all(case["passed"] for case in normalized),
        "case_count": len(normalized),
        "categories": categories,
        "failed_cases": [case["id"] for case in normalized if not case["passed"]],
        "cases": normalized,
    }
