from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .loader import EvaluationDataError


def evaluate_candidate_memory_fixture(path: Path) -> dict[str, Any]:
    """处理 evaluate candidate memory fixture。

    Args:
        path: path 参数。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationDataError("unable to load candidate memory fixture") from exc
    if not isinstance(payload, dict) or payload.get("version") != "v6-03":
        raise EvaluationDataError("candidate memory fixture version is invalid")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationDataError("candidate memory fixture cases are invalid")
    ids: list[str] = []
    normalized: list[dict[str, object]] = []
    for raw in cases:
        if not isinstance(raw, dict):
            raise EvaluationDataError("candidate memory fixture case is invalid")
        case_id = raw.get("id")
        expected = raw.get("expected")
        observed = raw.get("observed")
        eligible = raw.get("eligible")
        confirmed = raw.get("confirmed")
        if (
            not isinstance(case_id, str)
            or not case_id
            or expected not in {"active", "candidate", "rejected", "conflict"}
            or observed not in {"active", "candidate", "rejected", "conflict"}
            or not isinstance(eligible, bool)
            or not isinstance(confirmed, bool)
        ):
            raise EvaluationDataError("candidate memory fixture fields are invalid")
        ids.append(case_id)
        normalized.append(raw)
    if len(ids) != len(set(ids)):
        raise EvaluationDataError("candidate memory fixture ids are duplicated")

    created = [
        case
        for case in normalized
        if case["observed"] in {"active", "candidate", "conflict"}
    ]
    relevant_created = [case for case in created if case["eligible"] is True]
    forbidden = [case for case in normalized if case["expected"] == "rejected"]
    conflicts = [case for case in normalized if case["observed"] == "conflict"]
    confirmable = [
        case for case in normalized if case["observed"] in {"active", "candidate"}
    ]
    confirmed_cases = [case for case in confirmable if case["confirmed"] is True]
    mismatches = [
        str(case["id"]) for case in normalized if case["expected"] != case["observed"]
    ]
    return {
        "version": "v6-03",
        "passed": not mismatches,
        "mismatches": mismatches,
        "metrics": {
            "candidate_precision": _ratio(len(relevant_created), len(created)),
            "sensitive_rejection_rate": _ratio(
                sum(case["observed"] == "rejected" for case in forbidden),
                len(forbidden),
            ),
            "conflict_rate": _ratio(len(conflicts), len(created)),
            "confirmation_rate": _ratio(len(confirmed_cases), len(confirmable)),
        },
        "case_ids": ids,
    }


def _ratio(numerator: int, denominator: int) -> float:
    """执行 处理 ratio 的内部辅助逻辑。

    Args:
        numerator: numerator 参数。
        denominator: denominator 参数。
    """
    return 0.0 if denominator == 0 else numerator / denominator
