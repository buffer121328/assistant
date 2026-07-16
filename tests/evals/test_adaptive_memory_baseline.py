from __future__ import annotations

from pathlib import Path

from packages.evaluation.memory_baseline import (
    REQUIRED_COGNITIVE_TYPES,
    REQUIRED_SCOPES,
    REQUIRED_TIME_RANGES,
    evaluate_memory_baseline,
    load_memory_baseline_fixture,
)


DATASET = Path(__file__).parent / "datasets/adaptive_memory_v6_00.json"


def test_fixture_declares_v6_taxonomy_and_safe_compatibility_evidence() -> None:
    fixture = load_memory_baseline_fixture(DATASET)

    assert set(fixture["taxonomy"]["time_ranges"]) == REQUIRED_TIME_RANGES
    assert set(fixture["taxonomy"]["cognitive_types"]) == REQUIRED_COGNITIVE_TYPES
    assert set(fixture["taxonomy"]["scopes"]) == REQUIRED_SCOPES
    assert all(case["executable"] for case in fixture["legacy_tasks"])
    assert all(case["conversation_metadata"] is None for case in fixture["legacy_tasks"])
    assert all(case["memory_metadata"] is None for case in fixture["legacy_tasks"])


def test_report_contains_all_required_metrics_and_traceable_evidence() -> None:
    report = evaluate_memory_baseline(DATASET)
    payload = report.to_dict()

    assert payload["valid"] is True
    assert payload["version"] == "v6-00"
    assert payload["token_estimator"] == "unicode-unit-v1"
    assert set(payload["metrics"]) == {
        "compatibility",
        "isolation",
        "conversation_truncation",
        "preference_injection",
        "semantic_memory",
        "cross_session_accuracy",
        "stale_memory_use",
        "memory_command_gaps",
        "forbidden_memory",
    }
    assert payload["metrics"]["conversation_truncation"]["information_loss_rate"] == 1.0
    assert payload["metrics"]["preference_injection"]["irrelevant_item_ratio"] == 0.5
    assert payload["metrics"]["semantic_memory"]["enabled"]["recall"] == 0.8
    assert payload["metrics"]["semantic_memory"]["enabled"]["failure_rate"] == 1 / 3
    assert payload["metrics"]["cross_session_accuracy"]["overall"] == 0.75
    assert payload["metrics"]["stale_memory_use"]["rate"] == 0.5
    assert payload["metrics"]["memory_command_gaps"]["count"] == 3
    assert payload["metrics"]["forbidden_memory"]["rejection_rate"] == 1.0


def test_known_memory_failures_are_explicit_instead_of_expected_passes() -> None:
    report = evaluate_memory_baseline(DATASET).to_dict()
    failures = {item["case_id"]: item["reason"] for item in report["known_failures"]}

    assert "conflicting-active-preferences" in failures
    assert "long-conversation-critical-evidence" in failures
    assert "abstention-unknown-preference" in failures
    assert "stale-preference-used" in failures
    assert "semantic-enabled-timeout" in failures
    assert report["metrics"]["forbidden_memory"]["rejected_case_ids"] == [
        "synthetic-authorization-header",
        "synthetic-api-key",
    ]
    assert report["metrics"]["isolation"]["foreign_memory_ids"] == []
