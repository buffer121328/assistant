from __future__ import annotations

from dataclasses import dataclass

from .models import EvaluationRubric


@dataclass(frozen=True)
class RubricScore:
    score: float
    passed: bool
    reason: str


class DeterministicRubricMetric:
    """Framework-neutral deterministic checks used by pytest and local CI."""

    def __init__(self, rubric: EvaluationRubric) -> None:
        self.rubric = rubric

    def measure(self, actual_output: str) -> RubricScore:
        missing_required = tuple(
            phrase
            for phrase in self.rubric.required_phrases
            if phrase not in actual_output
        )
        present_forbidden = tuple(
            phrase
            for phrase in self.rubric.forbidden_phrases
            if phrase in actual_output
        )
        checks = [
            *(phrase not in missing_required for phrase in self.rubric.required_phrases),
            *(phrase not in present_forbidden for phrase in self.rubric.forbidden_phrases),
            len(actual_output) >= self.rubric.min_length,
            len(actual_output) <= self.rubric.max_length,
        ]
        score = round(sum(checks) / len(checks), 6)
        failures: list[str] = []
        if missing_required:
            failures.append("missing required phrases: " + ", ".join(missing_required))
        if present_forbidden:
            failures.append(
                "forbidden phrases present: " + ", ".join(present_forbidden)
            )
        if len(actual_output) < self.rubric.min_length:
            failures.append("output is shorter than the configured minimum")
        if len(actual_output) > self.rubric.max_length:
            failures.append("output is longer than the configured maximum")
        return RubricScore(
            score=score,
            passed=score >= self.rubric.threshold,
            reason=(
                "all deterministic rubric checks passed"
                if not failures
                else "; ".join(failures)
            ),
        )
