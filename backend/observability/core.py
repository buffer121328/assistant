from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from models import sanitize_text


ObservationType = Literal[
    "span",
    "agent",
    "tool",
    "generation",
    "evaluator",
]
ScoreValue = float | str | bool
ScoreType = Literal["NUMERIC", "CATEGORICAL", "BOOLEAN"]


class Observation(Protocol):
    """表示 处理 observation 的后端数据结构或服务对象。"""

    def update(
        self,
        *,
        output: object | None = None,
        error: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """更新。

        Args:
            output: output 参数。
            error: error 参数。
            metadata: metadata 参数。
        """
        ...


class Observability(Protocol):
    """表示 处理 observability 的后端数据结构或服务对象。"""

    def observe(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        input: object | None = None,
        metadata: Mapping[str, object] | None = None,
        model: str | None = None,
    ) -> AbstractContextManager[Observation]:
        """处理 observe。

        Args:
            name: name 参数。
            as_type: as_type 参数。
            input: input 参数。
            metadata: metadata 参数。
            model: model 参数。
        """
        ...

    def score(
        self,
        *,
        name: str,
        value: ScoreValue,
        trace_id: str | None = None,
        observation_id: str | None = None,
        data_type: ScoreType | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """处理 score。

        Args:
            name: name 参数。
            value: value 参数。
            trace_id: trace_id 参数。
            observation_id: observation_id 参数。
            data_type: data_type 参数。
            metadata: metadata 参数。
        """
        ...

    def flush(self) -> None:
        """刷新。"""
        ...

    def shutdown(self) -> None:
        """关闭。"""
        ...


@dataclass(frozen=True)
class NoopObservation:
    """表示 处理 noop observation 的后端数据结构或服务对象。"""

    def update(
        self,
        *,
        output: object | None = None,
        error: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """更新。

        Args:
            output: output 参数。
            error: error 参数。
            metadata: metadata 参数。
        """
        del output, error, metadata


class NoopObservability:
    """表示 处理 noop observability 的后端数据结构或服务对象。"""

    @contextmanager
    def observe(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        input: object | None = None,
        metadata: Mapping[str, object] | None = None,
        model: str | None = None,
    ) -> Iterator[Observation]:
        """处理 observe。

        Args:
            name: name 参数。
            as_type: as_type 参数。
            input: input 参数。
            metadata: metadata 参数。
            model: model 参数。
        """
        del name, as_type, input, metadata, model
        yield NoopObservation()

    def score(
        self,
        *,
        name: str,
        value: ScoreValue,
        trace_id: str | None = None,
        observation_id: str | None = None,
        data_type: ScoreType | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """处理 score。

        Args:
            name: name 参数。
            value: value 参数。
            trace_id: trace_id 参数。
            observation_id: observation_id 参数。
            data_type: data_type 参数。
            metadata: metadata 参数。
        """
        del name, value, trace_id, observation_id, data_type, metadata

    def flush(self) -> None:
        """刷新。"""
        return None

    def shutdown(self) -> None:
        """关闭。"""
        return None


def sanitize_telemetry_value(
    value: object,
    *,
    sensitive_values: tuple[str | None, ...] = (),
    depth: int = 0,
    max_depth: int = 4,
    max_items: int = 20,
    max_string_length: int = 1000,
) -> Any:
    """处理 sanitize telemetry value。

    Args:
        value: value 参数。
        sensitive_values: sensitive_values 参数。
        depth: depth 参数。
        max_depth: max_depth 参数。
        max_items: max_items 参数。
        max_string_length: max_string_length 参数。
    """
    if depth >= max_depth:
        return "[truncated]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return sanitize_text(
            value,
            extra_sensitive_values=sensitive_values,
        )[:max_string_length]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["[truncated]"] = "[truncated]"
                break
            safe_key = sanitize_text(
                key,
                extra_sensitive_values=sensitive_values,
            )[:100]
            result[safe_key] = sanitize_telemetry_value(
                item,
                sensitive_values=sensitive_values,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string_length=max_string_length,
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        list_result = [
            sanitize_telemetry_value(
                item,
                sensitive_values=sensitive_values,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string_length=max_string_length,
            )
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            list_result.append("[truncated]")
        return list_result
    return sanitize_text(
        value,
        extra_sensitive_values=sensitive_values,
    )[:max_string_length]
