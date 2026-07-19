from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from .loader import validate_safe_text


class ExperimentClient(Protocol):
    def run_experiment(
        self,
        *,
        name: str,
        data: list[dict[str, Any]],
        task: Callable[..., Any],
        metadata: dict[str, str] | None = None,
        run_name: str | None = None,
        description: str | None = None,
    ) -> Any: ...


def run_langfuse_experiment(
    *,
    client: ExperimentClient,
    name: str,
    items: Sequence[Mapping[str, Any]],
    task: Callable[..., Any],
    metadata: Mapping[str, str] | None = None,
    run_name: str | None = None,
    description: str | None = None,
) -> Any:
    """Run safe local items through a caller-supplied real task function."""

    if not name.strip():
        raise ValueError("Experiment name must not be empty")
    if not items:
        raise ValueError("Experiment items must not be empty")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        item_id = item.get("id")
        input_value = item.get("input")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError(f"Experiment item at index {index} requires an id")
        if input_value is None:
            raise ValueError(f"Experiment item '{item_id}' requires input")
        _validate_experiment_value(item_id, "input", input_value)
        _validate_experiment_value(
            item_id,
            "expected_output",
            item.get("expected_output"),
        )
        raw_metadata = item.get("metadata", {})
        if not isinstance(raw_metadata, Mapping):
            raise ValueError(f"Experiment item '{item_id}' has invalid metadata")
        _validate_experiment_value(item_id, "metadata", raw_metadata)
        normalized.append(
            {
                "input": input_value,
                "expected_output": item.get("expected_output"),
                "metadata": {**raw_metadata, "case_id": item_id.strip()},
            }
        )
    return client.run_experiment(
        name=name.strip(),
        data=normalized,
        task=task,
        metadata=dict(metadata) if metadata is not None else None,
        run_name=(run_name.strip() if isinstance(run_name, str) and run_name.strip() else None),
        description=(
            description.strip()
            if isinstance(description, str) and description.strip()
            else None
        ),
    )


def _validate_experiment_value(case_id: str, field: str, value: object) -> None:
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        validate_safe_text(case_id, field, value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_experiment_value(case_id, f"{field}.{key}", item)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_experiment_value(case_id, f"{field}.{index}", item)
        return
    raise ValueError(f"Experiment item '{case_id}' has unsupported {field}")
