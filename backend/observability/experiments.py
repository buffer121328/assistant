from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from evaluation.loader import validate_safe_text


class ExperimentClient(Protocol):
    """表示 处理 experiment client 的后端数据结构或服务对象。"""

    def run_experiment(
        self,
        *,
        name: str,
        data: list[dict[str, Any]],
        task: Callable[..., Any],
        metadata: dict[str, str] | None = None,
        run_name: str | None = None,
        description: str | None = None,
    ) -> Any:
        """运行 experiment。

        Args:
            name: name 参数。
            data: data 参数。
            task: task 参数。
            metadata: metadata 参数。
            run_name: run_name 参数。
            description: description 参数。
        """
        ...


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
    """Run safe local items through a caller-supplied real task function.

    Args:
        client: 用于执行当前操作的 client 参数。
        name: 目标对象或能力的名称。
        items: 用于执行当前操作的 items 参数。
        task: 需要处理的任务对象。
        metadata: 用于执行当前操作的 metadata 参数。
        run_name: 用于执行当前操作的 run name 参数。
        description: 用于执行当前操作的 description 参数。
    """
    normalized = _normalize_experiment_items(name=name, items=items)
    return client.run_experiment(
        name=name.strip(),
        data=normalized,
        task=task,
        metadata=dict(metadata) if metadata is not None else None,
        run_name=(
            run_name.strip() if isinstance(run_name, str) and run_name.strip() else None
        ),
        description=(
            description.strip()
            if isinstance(description, str) and description.strip()
            else None
        ),
    )


def run_langfuse_dataset_experiment(
    *,
    client: ExperimentClient,
    name: str,
    items: Sequence[Mapping[str, Any]],
    task: Callable[..., Any],
    scores: Mapping[str, Mapping[str, Any]] | None = None,
    metadata: Mapping[str, str] | None = None,
    run_name: str | None = None,
    description: str | None = None,
) -> Any:
    """Publish safe dataset items, run an experiment, and attach scores best effort.

    Args:
        client: 用于执行当前操作的 client 参数。
        name: 目标对象或能力的名称。
        items: 用于执行当前操作的 items 参数。
        task: 需要处理的任务对象。
        scores: 用于执行当前操作的 scores 参数。
        metadata: 用于执行当前操作的 metadata 参数。
        run_name: 用于执行当前操作的 run name 参数。
        description: 用于执行当前操作的 description 参数。
    """
    normalized = _normalize_experiment_items(name=name, items=items)
    dataset_metadata = dict(metadata) if metadata is not None else None
    if hasattr(client, "create_dataset"):
        client.create_dataset(name=name.strip(), metadata=dataset_metadata)
    if hasattr(client, "create_dataset_item"):
        for item in normalized:
            client.create_dataset_item(
                dataset_name=name.strip(),
                input=item["input"],
                expected_output=item.get("expected_output"),
                metadata=item.get("metadata"),
            )
    result = client.run_experiment(
        name=name.strip(),
        data=normalized,
        task=task,
        metadata=dataset_metadata,
        run_name=(
            run_name.strip() if isinstance(run_name, str) and run_name.strip() else None
        ),
        description=(
            description.strip()
            if isinstance(description, str) and description.strip()
            else None
        ),
    )
    _publish_scores_best_effort(client, scores or {})
    return result


def _normalize_experiment_items(
    *, name: str, items: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """将实验评分输入规范化为稳定的名称和值映射。

    Args:
        name: 目标对象或能力的名称。
        items: 用于执行当前操作的 items 参数。
    """
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
    return normalized


def _publish_scores_best_effort(
    client: object, scores: Mapping[str, Mapping[str, Any]]
) -> None:
    """尽力向可选观测后端发布评分，失败时不影响主流程。

    Args:
        client: 用于执行当前操作的 client 参数。
        scores: 用于执行当前操作的 scores 参数。
    """
    if not hasattr(client, "create_score"):
        return
    for case_id, score in scores.items():
        try:
            client.create_score(
                name=str(score.get("name", "deterministic_rubric")),
                value=score.get("value"),
                trace_id=score.get("trace_id"),
                data_type=score.get("data_type", "NUMERIC"),
                metadata={"case_id": case_id, **dict(score.get("metadata", {}))},
            )
        except Exception:
            continue


def _validate_experiment_value(case_id: str, field: str, value: object) -> None:
    """执行 校验 experiment value 的内部辅助逻辑。

    Args:
        case_id: case_id 参数。
        field: field 参数。
        value: value 参数。
    """
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
