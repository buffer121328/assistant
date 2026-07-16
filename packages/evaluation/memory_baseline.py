from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .loader import EvaluationDataError, validate_safe_text


REQUIRED_TIME_RANGES = frozenset({"execution", "conversation", "long_term"})
REQUIRED_COGNITIVE_TYPES = frozenset(
    {
        "profile",
        "fact",
        "preference",
        "episode",
        "procedure",
        "constraint",
        "working",
        "reflection",
    }
)
REQUIRED_SCOPES = frozenset(
    {
        "user/global",
        "user/project",
        "user/conversation",
        "agent/profile",
        "system/read_only",
    }
)
REQUIRED_SECTIONS = (
    "legacy_tasks",
    "isolation_cases",
    "conflict_cases",
    "forbidden_samples",
    "long_conversations",
    "preference_injection_cases",
    "semantic_modes",
    "cross_session_cases",
    "stale_memory_cases",
    "memory_command_gaps",
)
CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]")


@dataclass(frozen=True)
class MemoryBaselineReport:
    version: str
    token_estimator: str
    metrics: Mapping[str, Any]
    known_failures: tuple[Mapping[str, str], ...]

    @property
    def valid(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "valid": self.valid,
            "token_estimator": self.token_estimator,
            "metrics": dict(self.metrics),
            "known_failures": [dict(item) for item in self.known_failures],
        }


def load_memory_baseline_fixture(path: Path) -> dict[str, Any]:
    payload = _load_mapping(path)
    _validate_safe_values(payload)

    version = _required_string(payload.get("version"), "fixture", "version")
    if version != "v6-00":
        raise EvaluationDataError("memory baseline fixture has an unsupported version")
    estimator = _required_string(
        payload.get("token_estimator"), "fixture", "token_estimator"
    )
    if estimator != "unicode-unit-v1":
        raise EvaluationDataError("memory baseline fixture has an unsupported token estimator")

    taxonomy = _required_mapping(payload.get("taxonomy"), "fixture", "taxonomy")
    _require_exact_terms(taxonomy, "time_ranges", REQUIRED_TIME_RANGES)
    _require_exact_terms(taxonomy, "cognitive_types", REQUIRED_COGNITIVE_TYPES)
    _require_exact_terms(taxonomy, "scopes", REQUIRED_SCOPES)

    for section in REQUIRED_SECTIONS:
        cases = _required_list(payload.get(section), "fixture", section)
        _validate_case_ids(cases, section)

    _validate_fixture_shapes(payload)
    return dict(payload)


def evaluate_memory_baseline(path: Path) -> MemoryBaselineReport:
    fixture = load_memory_baseline_fixture(path)
    known_failures: list[dict[str, str]] = []

    compatibility = _compatibility_metrics(fixture["legacy_tasks"], known_failures)
    isolation = _isolation_metrics(fixture["isolation_cases"], known_failures)
    _record_conflicts(fixture["conflict_cases"], known_failures)
    forbidden = _forbidden_metrics(fixture["forbidden_samples"], known_failures)
    truncation = _truncation_metrics(fixture["long_conversations"], known_failures)
    injection = _preference_injection_metrics(fixture["preference_injection_cases"])
    semantic = _semantic_metrics(fixture["semantic_modes"], known_failures)
    cross_session = _cross_session_metrics(fixture["cross_session_cases"], known_failures)
    stale = _stale_metrics(fixture["stale_memory_cases"], known_failures)
    gaps = _command_gap_metrics(fixture["memory_command_gaps"])

    metrics = {
        "compatibility": compatibility,
        "isolation": isolation,
        "conversation_truncation": truncation,
        "preference_injection": injection,
        "semantic_memory": semantic,
        "cross_session_accuracy": cross_session,
        "stale_memory_use": stale,
        "memory_command_gaps": gaps,
        "forbidden_memory": forbidden,
    }
    return MemoryBaselineReport(
        version=cast(str, fixture["version"]),
        token_estimator=cast(str, fixture["token_estimator"]),
        metrics=metrics,
        known_failures=tuple(sorted(known_failures, key=lambda item: item["case_id"])),
    )


def _compatibility_metrics(
    raw_cases: object, known_failures: list[dict[str, str]]
) -> dict[str, Any]:
    cases = _mapping_cases(raw_cases)
    executable_ids = [case["id"] for case in cases if case["executable"] is True]
    for case in cases:
        if case["executable"] is not True:
            _failure(known_failures, case, "historical task is not executable")
    return {
        "executable_rate": _ratio(len(executable_ids), len(cases)),
        "case_ids": [case["id"] for case in cases],
        "metadata_migration_required": False,
    }


def _isolation_metrics(
    raw_cases: object, known_failures: list[dict[str, str]]
) -> dict[str, Any]:
    cases = _mapping_cases(raw_cases)
    foreign_ids: list[str] = []
    for case in cases:
        owned = cast(Mapping[str, Sequence[str]], case["owned_memory_ids"])
        observed = cast(Mapping[str, Sequence[str]], case["observed_memory_ids"])
        for user_id, observed_ids in observed.items():
            allowed = set(owned[user_id])
            leaked = sorted(set(observed_ids) - allowed)
            foreign_ids.extend(leaked)
            if leaked:
                _failure(known_failures, case, "cross-user memory leakage detected")
    return {
        "case_ids": [case["id"] for case in cases],
        "foreign_memory_ids": sorted(set(foreign_ids)),
    }


def _record_conflicts(
    raw_cases: object, known_failures: list[dict[str, str]]
) -> None:
    for case in _mapping_cases(raw_cases):
        if len(cast(Sequence[object], case["active_preference_ids"])) > 1 and case.get(
            "resolution_memory_id"
        ) is None:
            _failure(known_failures, case, "active preferences conflict without resolution")


def _forbidden_metrics(
    raw_cases: object, known_failures: list[dict[str, str]]
) -> dict[str, Any]:
    cases = _mapping_cases(raw_cases)
    rejected = [case["id"] for case in cases if case["observed_action"] == "reject"]
    for case in cases:
        if case["expected_action"] == "reject" and case["observed_action"] != "reject":
            _failure(known_failures, case, "forbidden memory sample was accepted")
    return {
        "rejection_rate": _ratio(len(rejected), len(cases)),
        "rejected_case_ids": rejected,
    }


def _truncation_metrics(
    raw_cases: object, known_failures: list[dict[str, str]]
) -> dict[str, Any]:
    cases = _mapping_cases(raw_cases)
    total_critical = 0
    truncated_critical = 0
    evidence: list[dict[str, Any]] = []
    impacted: list[str] = []
    for case in cases:
        messages = _mapping_cases(case["messages"])
        window_size = cast(int, case["window_size"])
        kept_start = max(0, len(messages) - window_size)
        removed = messages[:kept_start]
        removed_ids = [message["id"] for message in removed]
        critical_ids = [message["id"] for message in messages if message["critical"] is True]
        removed_critical_ids = [
            message["id"] for message in removed if message["critical"] is True
        ]
        total_critical += len(critical_ids)
        truncated_critical += len(removed_critical_ids)
        affected = bool(case["answer_affected"] and removed_critical_ids)
        if affected:
            impacted.append(cast(str, case["id"]))
            _failure(known_failures, case, "answer-critical evidence was truncated")
        evidence.append(
            {
                "case_id": case["id"],
                "truncated_message_ids": removed_ids,
                "truncated_critical_message_ids": removed_critical_ids,
                "answer_affected": affected,
            }
        )
    return {
        "information_loss_rate": _ratio(truncated_critical, total_critical),
        "impacted_case_ids": impacted,
        "evidence": evidence,
    }


def _preference_injection_metrics(raw_cases: object) -> dict[str, Any]:
    cases = _mapping_cases(raw_cases)
    total_items = 0
    irrelevant_items = 0
    total_tokens = 0
    evidence: list[dict[str, Any]] = []
    for case in cases:
        preferences = _mapping_cases(case["preferences"])
        case_tokens = sum(_estimate_tokens(cast(str, item["text"])) for item in preferences)
        case_irrelevant = sum(item["relevant"] is False for item in preferences)
        total_items += len(preferences)
        irrelevant_items += case_irrelevant
        total_tokens += case_tokens
        evidence.append(
            {
                "case_id": case["id"],
                "preference_ids": [item["id"] for item in preferences],
                "estimated_tokens": case_tokens,
                "irrelevant_items": case_irrelevant,
            }
        )
    return {
        "average_estimated_tokens": _average(total_tokens, len(cases)),
        "irrelevant_item_ratio": _ratio(irrelevant_items, total_items),
        "estimator_version": "unicode-unit-v1",
        "evidence": evidence,
    }


def _semantic_metrics(
    raw_modes: object, known_failures: list[dict[str, str]]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for mode in _mapping_cases(raw_modes):
        cases = _mapping_cases(mode["cases"])
        relevant_total = sum(cast(int, case["relevant_total"]) for case in cases)
        retrieved = sum(cast(int, case["retrieved_relevant"]) for case in cases)
        failed = [case for case in cases if case["failed"] is True]
        for case in failed:
            _failure(known_failures, case, "semantic memory observation failed")
        result[cast(str, mode["mode"])] = {
            "recall": _ratio(retrieved, relevant_total),
            "average_latency_ms": _average(
                sum(cast(int, case["latency_ms"]) for case in cases), len(cases)
            ),
            "failure_rate": _ratio(len(failed), len(cases)),
            "case_ids": [case["id"] for case in cases],
        }
    return result


def _cross_session_metrics(
    raw_cases: object, known_failures: list[dict[str, str]]
) -> dict[str, Any]:
    cases = _mapping_cases(raw_cases)
    categories: dict[str, dict[str, Any]] = {}
    for case in cases:
        category = cast(str, case["category"])
        bucket = categories.setdefault(category, {"correct": 0, "total": 0, "case_ids": []})
        bucket["total"] += 1
        bucket["case_ids"].append(case["id"])
        if case["correct"] is True:
            bucket["correct"] += 1
        else:
            _failure(known_failures, case, "cross-session answer was incorrect")
    normalized = {
        category: {
            "accuracy": _ratio(cast(int, values["correct"]), cast(int, values["total"])),
            "case_ids": values["case_ids"],
        }
        for category, values in sorted(categories.items())
    }
    return {
        "overall": _ratio(sum(case["correct"] is True for case in cases), len(cases)),
        "categories": normalized,
    }


def _stale_metrics(
    raw_cases: object, known_failures: list[dict[str, str]]
) -> dict[str, Any]:
    cases = _mapping_cases(raw_cases)
    stale = [case for case in cases if case["used_stale_memory"] is True]
    for case in stale:
        _failure(known_failures, case, "stale memory affected the answer")
    return {
        "rate": _ratio(len(stale), len(cases)),
        "stale_case_ids": [case["id"] for case in stale],
    }


def _command_gap_metrics(raw_cases: object) -> dict[str, Any]:
    cases = _mapping_cases(raw_cases)
    return {
        "count": len(cases),
        "case_ids": [case["id"] for case in cases],
        "by_command": {
            command: sum(case["command"] == command for case in cases)
            for command in ("write", "list", "delete")
        },
    }


def _validate_fixture_shapes(payload: Mapping[str, Any]) -> None:
    for case in _mapping_cases(payload["legacy_tasks"]):
        _required_bool(case.get("executable"), cast(str, case["id"]), "executable")
        if case.get("conversation_metadata") is not None or case.get("memory_metadata") is not None:
            raise EvaluationDataError(
                f"memory baseline case '{case['id']}' must omit legacy metadata"
            )
    for case in _mapping_cases(payload["isolation_cases"]):
        owned = _required_mapping(case.get("owned_memory_ids"), cast(str, case["id"]), "owned_memory_ids")
        observed = _required_mapping(case.get("observed_memory_ids"), cast(str, case["id"]), "observed_memory_ids")
        if set(owned) != set(observed):
            raise EvaluationDataError(f"memory baseline case '{case['id']}' has mismatched users")
        for user_id in owned:
            _string_list(owned[user_id], cast(str, case["id"]), f"owned_memory_ids.{user_id}")
            _string_list(observed[user_id], cast(str, case["id"]), f"observed_memory_ids.{user_id}")
    for case in _mapping_cases(payload["conflict_cases"]):
        _string_list(case.get("active_preference_ids"), cast(str, case["id"]), "active_preference_ids")
        resolution = case.get("resolution_memory_id")
        if resolution is not None and not isinstance(resolution, str):
            raise EvaluationDataError(f"memory baseline case '{case['id']}' has invalid resolution")
    for case in _mapping_cases(payload["forbidden_samples"]):
        _required_string(case.get("sample_kind"), cast(str, case["id"]), "sample_kind")
        _required_string(case.get("placeholder"), cast(str, case["id"]), "placeholder")
        for field in ("expected_action", "observed_action"):
            if _required_string(case.get(field), cast(str, case["id"]), field) not in {"reject", "accept"}:
                raise EvaluationDataError(f"memory baseline case '{case['id']}' has invalid {field}")
    for case in _mapping_cases(payload["long_conversations"]):
        _positive_int(case.get("window_size"), cast(str, case["id"]), "window_size")
        _required_bool(case.get("answer_affected"), cast(str, case["id"]), "answer_affected")
        messages = _required_list(case.get("messages"), cast(str, case["id"]), "messages")
        _validate_case_ids(messages, f"{case['id']}.messages")
        for message in _mapping_cases(messages):
            _required_bool(message.get("critical"), cast(str, message["id"]), "critical")
    for case in _mapping_cases(payload["preference_injection_cases"]):
        preferences = _required_list(case.get("preferences"), cast(str, case["id"]), "preferences")
        _validate_case_ids(preferences, f"{case['id']}.preferences")
        for item in _mapping_cases(preferences):
            _required_string(item.get("text"), cast(str, item["id"]), "text")
            _required_bool(item.get("relevant"), cast(str, item["id"]), "relevant")
    modes = _mapping_cases(payload["semantic_modes"])
    if {mode.get("mode") for mode in modes} != {"enabled", "disabled"}:
        raise EvaluationDataError("memory baseline fixture requires enabled and disabled semantic modes")
    for mode in modes:
        cases = _required_list(mode.get("cases"), cast(str, mode["id"]), "cases") if "id" in mode else _required_list(mode.get("cases"), cast(str, mode["mode"]), "cases")
        for case in _mapping_cases(cases):
            relevant = _non_negative_int(case.get("relevant_total"), cast(str, case["id"]), "relevant_total")
            retrieved = _non_negative_int(case.get("retrieved_relevant"), cast(str, case["id"]), "retrieved_relevant")
            if retrieved > relevant:
                raise EvaluationDataError(f"memory baseline case '{case['id']}' retrieves more relevant items than exist")
            _non_negative_int(case.get("latency_ms"), cast(str, case["id"]), "latency_ms")
            _required_bool(case.get("failed"), cast(str, case["id"]), "failed")
    for case in _mapping_cases(payload["cross_session_cases"]):
        if _required_string(case.get("category"), cast(str, case["id"]), "category") not in {"preference", "knowledge_update", "temporal", "abstention"}:
            raise EvaluationDataError(f"memory baseline case '{case['id']}' has invalid category")
        _required_bool(case.get("correct"), cast(str, case["id"]), "correct")
    for case in _mapping_cases(payload["stale_memory_cases"]):
        _required_bool(case.get("used_stale_memory"), cast(str, case["id"]), "used_stale_memory")
    for case in _mapping_cases(payload["memory_command_gaps"]):
        if _required_string(case.get("command"), cast(str, case["id"]), "command") not in {"write", "list", "delete"}:
            raise EvaluationDataError(f"memory baseline case '{case['id']}' has invalid command")
        if _required_string(case.get("severity"), cast(str, case["id"]), "severity") not in {"low", "medium", "high"}:
            raise EvaluationDataError(f"memory baseline case '{case['id']}' has invalid severity")


def _load_mapping(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationDataError(f"unable to load memory baseline JSON '{path.name}'") from exc
    if not isinstance(payload, Mapping):
        raise EvaluationDataError(f"memory baseline JSON '{path.name}' must be an object")
    return cast(Mapping[str, Any], payload)


def _validate_safe_values(value: object, *, case_id: str = "fixture", field: str = "root") -> None:
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        validate_safe_text(case_id, field, value)
        return
    if isinstance(value, Mapping):
        next_case_id = value.get("id") if isinstance(value.get("id"), str) else case_id
        for key, item in value.items():
            _validate_safe_values(item, case_id=cast(str, next_case_id), field=f"{field}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for index, item in enumerate(value):
            _validate_safe_values(item, case_id=case_id, field=f"{field}.{index}")
        return
    raise EvaluationDataError(f"memory baseline case '{case_id}' has unsupported data")


def _require_exact_terms(
    taxonomy: Mapping[str, Any], field: str, expected: frozenset[str]
) -> None:
    actual = set(_string_list(taxonomy.get(field), "taxonomy", field))
    if actual != expected:
        raise EvaluationDataError(f"memory baseline taxonomy has invalid {field}")


def _validate_case_ids(cases: Sequence[object], section: str) -> None:
    ids: list[str] = []
    for raw_case in cases:
        if not isinstance(raw_case, Mapping):
            raise EvaluationDataError(f"memory baseline section '{section}' has an invalid case")
        case_id = _required_string(raw_case.get("id"), section, "id")
        if not CASE_ID_PATTERN.fullmatch(case_id):
            raise EvaluationDataError(f"memory baseline section '{section}' has an invalid case id")
        ids.append(case_id)
    if len(ids) != len(set(ids)):
        raise EvaluationDataError(f"memory baseline section '{section}' has duplicate case ids")


def _mapping_cases(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise EvaluationDataError("memory baseline fixture has an invalid case collection")
    if not all(isinstance(item, Mapping) for item in value):
        raise EvaluationDataError("memory baseline fixture has an invalid case")
    return [cast(Mapping[str, Any], item) for item in value]


def _required_mapping(value: object, case_id: str, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationDataError(f"memory baseline case '{case_id}' has invalid {field}")
    return cast(Mapping[str, Any], value)


def _required_list(value: object, case_id: str, field: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise EvaluationDataError(f"memory baseline case '{case_id}' has invalid {field}")
    return value


def _string_list(value: object, case_id: str, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise EvaluationDataError(f"memory baseline case '{case_id}' has invalid {field}")
    parsed = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(parsed) != len(value):
        raise EvaluationDataError(f"memory baseline case '{case_id}' has invalid {field}")
    return parsed


def _required_string(value: object, case_id: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationDataError(f"memory baseline case '{case_id}' requires {field}")
    return value.strip()


def _required_bool(value: object, case_id: str, field: str) -> bool:
    if not isinstance(value, bool):
        raise EvaluationDataError(f"memory baseline case '{case_id}' has invalid {field}")
    return value


def _positive_int(value: object, case_id: str, field: str) -> int:
    parsed = _non_negative_int(value, case_id, field)
    if parsed == 0:
        raise EvaluationDataError(f"memory baseline case '{case_id}' has invalid {field}")
    return parsed


def _non_negative_int(value: object, case_id: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvaluationDataError(f"memory baseline case '{case_id}' has invalid {field}")
    return value


def _estimate_tokens(text: str) -> int:
    return len(TOKEN_PATTERN.findall(text))


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _average(total: int, count: int) -> float:
    return 0.0 if count == 0 else total / count


def _failure(
    failures: list[dict[str, str]], case: Mapping[str, Any], reason: str
) -> None:
    failures.append({"case_id": cast(str, case["id"]), "reason": reason})
