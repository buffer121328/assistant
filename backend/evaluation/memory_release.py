from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
import math
from pathlib import Path
import re
from typing import Any, cast

from .loader import EvaluationDataError


_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_REQUIRED_THRESHOLDS = (
    "min_accuracy",
    "min_evidence_precision",
    "min_evidence_recall",
    "min_abstention_accuracy",
    "max_stale_use_rate",
    "max_contradiction_rate",
    "max_p95_latency_ms",
    "max_average_context_tokens",
    "max_sql_index_drift",
)
_REQUIRED_MANUAL_EVIDENCE = frozenset(
    {"knowledge_update", "correction", "forgetting", "long_session_compaction"}
)


def evaluate_memory_release_fixture(
    path: Path, *, manual_evidence_path: Path | None = None
) -> dict[str, Any]:
    """处理 evaluate memory release fixture。

    Args:
        path: path 参数。
        manual_evidence_path: manual_evidence_path 参数。
    """
    payload = _load_fixture(path)
    thresholds = _parse_thresholds(payload.get("thresholds"))
    cases = _parse_cases(payload.get("cases"))
    provided_evidence = _manual_evidence(payload.get("manual_evidence"))
    if manual_evidence_path is not None:
        provided_evidence |= _load_manual_evidence_manifest(manual_evidence_path)
    manual_complete = provided_evidence == _REQUIRED_MANUAL_EVIDENCE

    count = len(cases)
    evidence_true_positive = sum(case["evidence_true_positive"] for case in cases)
    evidence_retrieved = sum(case["evidence_retrieved"] for case in cases)
    evidence_expected = sum(case["evidence_expected"] for case in cases)
    abstention_cases = [case for case in cases if case["abstention_expected"]]
    latencies = sorted(float(case["latency_ms"]) for case in cases)
    p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1)
    feedback_cases = [case for case in cases if case["feedback"] != "none"]

    metrics: dict[str, int | float] = {
        "accuracy": _ratio(sum(case["accurate"] for case in cases), count),
        "evidence_precision": _ratio(
            evidence_true_positive, evidence_retrieved, empty=1.0
        ),
        "evidence_recall": _ratio(evidence_true_positive, evidence_expected, empty=1.0),
        "stale_use_rate": _ratio(sum(case["stale_used"] for case in cases), count),
        "contradiction_rate": _ratio(
            sum(case["contradiction_injected"] for case in cases), count
        ),
        "abstention_accuracy": _ratio(
            sum(case["abstained"] for case in abstention_cases),
            len(abstention_cases),
            empty=1.0,
        ),
        "cross_user_leak_count": sum(case["cross_user_leak"] for case in cases),
        "forbidden_write_count": sum(case["forbidden_write"] for case in cases),
        "p50_latency_ms": latencies[(len(latencies) - 1) // 2],
        "p95_latency_ms": latencies[p95_index],
        "average_context_tokens": sum(case["context_tokens"] for case in cases) / count,
        "helpful_rate": _ratio(
            sum(case["feedback"] == "helpful" for case in feedback_cases),
            len(feedback_cases),
        ),
        "harmful_rate": _ratio(
            sum(case["feedback"] == "harmful" for case in feedback_cases),
            len(feedback_cases),
        ),
        "sql_index_drift": sum(case["sql_index_drift"] for case in cases),
    }
    reasons = _gate_reasons(metrics=metrics, thresholds=thresholds)
    automated_passed = not reasons
    if not manual_complete:
        reasons.append("manual_evidence_pending")
    return {
        "valid": True,
        "version": "v6-07",
        "passed": not reasons,
        "automated_passed": automated_passed,
        "manual_evidence_complete": manual_complete,
        "gate_reasons": reasons,
        "metrics": metrics,
        "case_ids": [case["id"] for case in cases],
    }


def _load_fixture(path: Path) -> Mapping[str, Any]:
    """执行 加载 fixture 的内部辅助逻辑。

    Args:
        path: path 参数。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationDataError("unable to load release fixture") from exc
    if not isinstance(payload, Mapping) or payload.get("version") != "v6-07":
        raise EvaluationDataError("release fixture version is invalid")
    return cast(Mapping[str, Any], payload)


def _parse_thresholds(value: object) -> dict[str, float]:
    """执行 解析 thresholds 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if not isinstance(value, Mapping) or set(value) != set(_REQUIRED_THRESHOLDS):
        raise EvaluationDataError("release fixture thresholds are invalid")
    thresholds: dict[str, float] = {}
    for key in _REQUIRED_THRESHOLDS:
        raw = value[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise EvaluationDataError("release fixture threshold value is invalid")
        parsed = float(raw)
        if parsed < 0:
            raise EvaluationDataError("release fixture threshold value is invalid")
        if key.startswith("min_") or key.endswith("_rate"):
            if parsed > 1:
                raise EvaluationDataError("release fixture threshold value is invalid")
        thresholds[key] = parsed
    return thresholds


def _parse_cases(value: object) -> list[dict[str, Any]]:
    """执行 解析 cases 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if not isinstance(value, list) or not value:
        raise EvaluationDataError("release fixture cases are invalid")
    parsed: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise EvaluationDataError("release fixture case is invalid")
        case_id = raw.get("id")
        if (
            not isinstance(case_id, str)
            or not _CASE_ID.fullmatch(case_id)
            or case_id in ids
        ):
            raise EvaluationDataError("release fixture case id is invalid")
        ids.add(case_id)
        booleans = (
            "accurate",
            "stale_used",
            "contradiction_injected",
            "abstention_expected",
            "abstained",
            "cross_user_leak",
            "forbidden_write",
        )
        if any(not isinstance(raw.get(key), bool) for key in booleans):
            raise EvaluationDataError("release fixture case boolean is invalid")
        integers = (
            "evidence_true_positive",
            "evidence_retrieved",
            "evidence_expected",
            "context_tokens",
            "sql_index_drift",
        )
        if any(
            isinstance(raw.get(key), bool)
            or not isinstance(raw.get(key), int)
            or int(raw[key]) < 0
            for key in integers
        ):
            raise EvaluationDataError("release fixture case integer is invalid")
        if int(raw["evidence_true_positive"]) > int(raw["evidence_retrieved"]) or int(
            raw["evidence_true_positive"]
        ) > int(raw["evidence_expected"]):
            raise EvaluationDataError("release fixture evidence counts are invalid")
        latency = raw.get("latency_ms")
        if (
            isinstance(latency, bool)
            or not isinstance(latency, (int, float))
            or latency < 0
        ):
            raise EvaluationDataError("release fixture latency is invalid")
        if raw.get("feedback") not in {"helpful", "harmful", "none"}:
            raise EvaluationDataError("release fixture feedback is invalid")
        parsed.append(dict(raw))
    return parsed


def _manual_evidence(value: object) -> set[str]:
    """执行 处理 manual evidence 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if not isinstance(value, Mapping):
        raise EvaluationDataError("release fixture manual evidence is invalid")
    required = value.get("required")
    provided = value.get("provided")
    if (
        not isinstance(required, list)
        or not isinstance(provided, list)
        or any(not isinstance(item, str) for item in required + provided)
        or len(required) != len(set(required))
        or len(provided) != len(set(provided))
        or set(required) != _REQUIRED_MANUAL_EVIDENCE
        or not set(provided).issubset(_REQUIRED_MANUAL_EVIDENCE)
    ):
        raise EvaluationDataError("release fixture manual evidence is invalid")
    return set(provided)


def _load_manual_evidence_manifest(path: Path) -> set[str]:
    """执行 加载 manual evidence manifest 的内部辅助逻辑。

    Args:
        path: path 参数。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationDataError("unable to load release evidence manifest") from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("format") != "v6-local-trial-evidence-v1"
    ):
        raise EvaluationDataError("release evidence manifest format is invalid")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        raise EvaluationDataError("release evidence manifest entries are invalid")
    provided: set[str] = set()
    for raw in evidence:
        if not isinstance(raw, Mapping) or set(raw) != {
            "type",
            "evidence_id",
            "observed_at",
        }:
            raise EvaluationDataError("release evidence manifest entry is invalid")
        evidence_type = raw.get("type")
        evidence_id = raw.get("evidence_id")
        observed_at = raw.get("observed_at")
        if (
            evidence_type not in _REQUIRED_MANUAL_EVIDENCE
            or not isinstance(evidence_id, str)
            or not _CASE_ID.fullmatch(evidence_id)
            or not isinstance(observed_at, str)
        ):
            raise EvaluationDataError("release evidence manifest entry is invalid")
        try:
            datetime.fromisoformat(observed_at)
        except ValueError as exc:
            raise EvaluationDataError(
                "release evidence manifest timestamp is invalid"
            ) from exc
        if evidence_type in provided:
            raise EvaluationDataError("release evidence manifest type is duplicated")
        provided.add(cast(str, evidence_type))
    return provided


def _gate_reasons(
    *, metrics: Mapping[str, int | float], thresholds: Mapping[str, float]
) -> list[str]:
    """执行 处理 gate reasons 的内部辅助逻辑。

    Args:
        metrics: metrics 参数。
        thresholds: thresholds 参数。
    """
    checks = (
        (metrics["cross_user_leak_count"] > 0, "cross_user_leak_nonzero"),
        (metrics["forbidden_write_count"] > 0, "forbidden_write_nonzero"),
        (
            metrics["stale_use_rate"] > thresholds["max_stale_use_rate"],
            "stale_use_rate_exceeded",
        ),
        (
            metrics["contradiction_rate"] > thresholds["max_contradiction_rate"],
            "contradiction_rate_exceeded",
        ),
        (metrics["accuracy"] < thresholds["min_accuracy"], "accuracy_below_minimum"),
        (
            metrics["evidence_precision"] < thresholds["min_evidence_precision"],
            "evidence_precision_below_minimum",
        ),
        (
            metrics["evidence_recall"] < thresholds["min_evidence_recall"],
            "evidence_recall_below_minimum",
        ),
        (
            metrics["abstention_accuracy"] < thresholds["min_abstention_accuracy"],
            "abstention_accuracy_below_minimum",
        ),
        (
            metrics["p95_latency_ms"] > thresholds["max_p95_latency_ms"],
            "p95_latency_exceeded",
        ),
        (
            metrics["average_context_tokens"]
            > thresholds["max_average_context_tokens"],
            "average_context_tokens_exceeded",
        ),
        (
            metrics["sql_index_drift"] > thresholds["max_sql_index_drift"],
            "sql_index_drift_exceeded",
        ),
    )
    return [reason for failed, reason in checks if failed]


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    """执行 处理 ratio 的内部辅助逻辑。

    Args:
        numerator: numerator 参数。
        denominator: denominator 参数。
        empty: empty 参数。
    """
    return empty if denominator == 0 else numerator / denominator
