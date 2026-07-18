from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from .models import (
    EvaluationBaseline,
    EvaluationCase,
    EvaluationRubric,
    TaskType,
)


SUPPORTED_TASK_TYPES = frozenset({"plan", "learn", "daily", "office", "safety"})
CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
URL_PATTERN = re.compile(r"https?://[^\s)\]>]+", re.IGNORECASE)
IPV4_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:authorization|proxy-authorization)\s*:", re.IGNORECASE),
    re.compile(r"\bcookie\s*:", re.IGNORECASE),
    re.compile(r"\bbearer\s+\S+", re.IGNORECASE),
    re.compile(r"\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]", re.IGNORECASE),
    re.compile(r"\bsk-[a-z0-9_-]{8,}", re.IGNORECASE),
)
PRIVATE_HOST_SUFFIXES = (".internal", ".lan", ".local")


class EvaluationDataError(ValueError):
    """Raised when evaluation input does not match the local JSON contract."""


class DatasetSecurityError(EvaluationDataError):
    """Raised when evaluation input may contain sensitive or private content."""


def load_cases(path: Path) -> tuple[EvaluationCase, ...]:
    payload = _load_mapping(path)
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvaluationDataError("evaluation dataset must contain a non-empty cases list")

    cases = tuple(_parse_case(raw_case, index) for index, raw_case in enumerate(raw_cases))
    case_ids = [case.id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise EvaluationDataError("evaluation dataset contains duplicate case ids")
    return cases


def load_candidate_outputs(path: Path) -> dict[str, str]:
    payload = _load_mapping(path)
    raw_outputs = payload.get("outputs")
    if not isinstance(raw_outputs, Mapping) or not raw_outputs:
        raise EvaluationDataError("candidate file must contain a non-empty outputs mapping")

    outputs: dict[str, str] = {}
    for raw_case_id, raw_output in raw_outputs.items():
        if not isinstance(raw_case_id, str) or not CASE_ID_PATTERN.fullmatch(raw_case_id):
            raise EvaluationDataError("candidate file contains an invalid case id")
        output = _required_string(raw_output, raw_case_id, "actual_output")
        validate_safe_text(raw_case_id, "actual_output", output)
        outputs[raw_case_id] = output
    return outputs


def load_baseline(path: Path) -> EvaluationBaseline:
    payload = _load_mapping(path)
    version = _required_string(payload.get("version"), "baseline", "version")
    raw_scores = payload.get("scores")
    if not isinstance(raw_scores, Mapping) or not raw_scores:
        raise EvaluationDataError("evaluation baseline must contain a non-empty scores mapping")

    scores: dict[str, float] = {}
    for raw_case_id, raw_score in raw_scores.items():
        if not isinstance(raw_case_id, str) or not CASE_ID_PATTERN.fullmatch(raw_case_id):
            raise EvaluationDataError("evaluation baseline contains an invalid case id")
        scores[raw_case_id] = _bounded_float(raw_score, raw_case_id, "baseline score")
    return EvaluationBaseline(version=version, scores=scores)


def _parse_case(raw_case: object, index: int) -> EvaluationCase:
    if not isinstance(raw_case, Mapping):
        raise EvaluationDataError(f"evaluation case at index {index} must be an object")

    case_id = _required_string(raw_case.get("id"), f"index-{index}", "id")
    if not CASE_ID_PATTERN.fullmatch(case_id):
        raise EvaluationDataError(f"evaluation case '{case_id}' has an invalid id")

    task_type_value = _required_string(raw_case.get("task_type"), case_id, "task_type")
    if task_type_value not in SUPPORTED_TASK_TYPES:
        raise EvaluationDataError(f"evaluation case '{case_id}' has an unsupported task type")

    input_text = _required_string(raw_case.get("input"), case_id, "input")
    actual_output = _required_string(
        raw_case.get("actual_output"), case_id, "actual_output"
    )
    validate_safe_text(case_id, "input", input_text)
    validate_safe_text(case_id, "actual_output", actual_output)

    raw_rubric = raw_case.get("rubric")
    if not isinstance(raw_rubric, Mapping):
        raise EvaluationDataError(f"evaluation case '{case_id}' must define a rubric")
    rubric = _parse_rubric(case_id, raw_rubric)

    return EvaluationCase(
        id=case_id,
        task_type=cast(TaskType, task_type_value),
        input=input_text,
        actual_output=actual_output,
        rubric=rubric,
    )


def _parse_rubric(case_id: str, raw_rubric: Mapping[object, object]) -> EvaluationRubric:
    required_phrases = _string_tuple(
        raw_rubric.get("required_phrases"), case_id, "required_phrases", required=True
    )
    forbidden_phrases = _string_tuple(
        raw_rubric.get("forbidden_phrases", []),
        case_id,
        "forbidden_phrases",
        required=False,
    )
    min_length = _positive_int(raw_rubric.get("min_length"), case_id, "min_length")
    max_length = _positive_int(raw_rubric.get("max_length"), case_id, "max_length")
    if max_length < min_length:
        raise EvaluationDataError(
            f"evaluation case '{case_id}' has max_length below min_length"
        )
    threshold = _bounded_float(raw_rubric.get("threshold"), case_id, "threshold")
    return EvaluationRubric(
        required_phrases=required_phrases,
        forbidden_phrases=forbidden_phrases,
        min_length=min_length,
        max_length=max_length,
        threshold=threshold,
    )


def _load_mapping(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationDataError(f"unable to load evaluation JSON '{path.name}'") from exc
    if not isinstance(payload, Mapping):
        raise EvaluationDataError(f"evaluation JSON '{path.name}' must be an object")
    return cast(Mapping[str, Any], payload)


def _required_string(value: object, case_id: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationDataError(
            f"evaluation case '{case_id}' requires a non-empty {field}"
        )
    return value.strip()


def _string_tuple(
    value: object,
    case_id: str,
    field: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (required and not value):
        raise EvaluationDataError(f"evaluation case '{case_id}' has an invalid {field}")
    parsed = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(parsed) != len(value) or (required and not parsed):
        raise EvaluationDataError(f"evaluation case '{case_id}' has an invalid {field}")
    return parsed


def _positive_int(value: object, case_id: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EvaluationDataError(f"evaluation case '{case_id}' has an invalid {field}")
    return value


def _bounded_float(value: object, case_id: str, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationDataError(f"evaluation case '{case_id}' has an invalid {field}")
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise EvaluationDataError(f"evaluation case '{case_id}' has an invalid {field}")
    return parsed


def validate_safe_text(case_id: str, field: str, value: str) -> None:
    if any(pattern.search(value) for pattern in SENSITIVE_PATTERNS):
        raise DatasetSecurityError(
            f"evaluation case '{case_id}' contains prohibited content in {field}"
        )

    for raw_url in URL_PATTERN.findall(value):
        hostname = (urlparse(raw_url).hostname or "").lower()
        if _is_private_hostname(hostname):
            raise DatasetSecurityError(
                f"evaluation case '{case_id}' contains a private URL in {field}"
            )

    for raw_ip in IPV4_PATTERN.findall(value):
        try:
            address = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if not address.is_global:
            raise DatasetSecurityError(
                f"evaluation case '{case_id}' contains a private address in {field}"
            )


def _is_private_hostname(hostname: str) -> bool:
    if hostname == "localhost" or hostname.endswith(PRIVATE_HOST_SUFFIXES):
        return True
    try:
        return not ipaddress.ip_address(hostname).is_global
    except ValueError:
        return False
