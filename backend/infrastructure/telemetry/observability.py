from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Callable, Literal, Protocol, cast

from domain.policies.redaction import sanitize_text
from infrastructure.settings.config import Settings


logger = logging.getLogger(__name__)

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


class LangfuseObservationClient(Protocol):
    """表示 处理 langfuse observation client 的后端数据结构或服务对象。"""

    def update(self, **kwargs: Any) -> Any:
        """更新。

        Args:
            kwargs: kwargs 参数。
        """
        ...


class LangfuseClient(Protocol):
    """表示 处理 langfuse client 的后端数据结构或服务对象。"""

    def start_as_current_observation(
        self, **kwargs: Any
    ) -> AbstractContextManager[Any]:
        """启动 as current observation。

        Args:
            kwargs: kwargs 参数。
        """
        ...

    def create_score(self, **kwargs: Any) -> None:
        """创建 score。

        Args:
            kwargs: kwargs 参数。
        """
        ...

    def flush(self) -> None:
        """刷新。"""
        ...

    def shutdown(self) -> None:
        """关闭。"""
        ...


class _LangfuseObservation:
    """表示 处理 langfuse observation 的后端数据结构或服务对象。"""

    def __init__(
        self,
        client: LangfuseObservationClient,
        *,
        sensitive_values: tuple[str | None, ...],
    ) -> None:
        """初始化对象实例。

        Args:
            client: client 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.client = client
        self.sensitive_values = sensitive_values

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
        payload: dict[str, Any] = {}
        if output is not None:
            payload["output"] = self._safe(output)
        if metadata is not None:
            payload["metadata"] = self._safe(metadata)
        if error is not None:
            payload["level"] = "ERROR"
            payload["status_message"] = self._safe(error)
        if not payload:
            return
        try:
            self.client.update(**payload)
        except Exception:
            _warn("Langfuse observation update failed")

    def _safe(self, value: object) -> Any:
        """执行 处理 safe 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        return sanitize_telemetry_value(
            value,
            sensitive_values=self.sensitive_values,
        )


class LangfuseObservability:
    """表示 处理 langfuse observability 的后端数据结构或服务对象。"""

    def __init__(
        self,
        client: LangfuseClient,
        *,
        sensitive_values: tuple[str | None, ...] = (),
    ) -> None:
        """初始化对象实例。

        Args:
            client: client 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.client = client
        self.sensitive_values = sensitive_values

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
        kwargs: dict[str, Any] = {
            "name": self._safe(name),
            "as_type": as_type,
        }
        if input is not None:
            kwargs["input"] = self._safe(input)
        if metadata is not None:
            kwargs["metadata"] = self._safe(metadata)
        if model is not None:
            kwargs["model"] = self._safe(model)
        try:
            manager = self.client.start_as_current_observation(**kwargs)
            sdk_observation = manager.__enter__()
        except Exception:
            _warn("Langfuse observation start failed")
            yield NoopObservation()
            return

        observation = _LangfuseObservation(
            sdk_observation,
            sensitive_values=self.sensitive_values,
        )
        try:
            yield observation
        except BaseException as exc:
            observation.update(error=exc)
            self._safe_exit(manager, type(exc), exc, exc.__traceback__)
            raise
        else:
            self._safe_exit(manager, None, None, None)

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
        kwargs: dict[str, Any] = {
            "name": self._safe(name),
            "value": value,
        }
        if trace_id is not None:
            kwargs["trace_id"] = self._safe(trace_id)
        if observation_id is not None:
            kwargs["observation_id"] = self._safe(observation_id)
        if data_type is not None:
            kwargs["data_type"] = data_type
        if metadata is not None:
            kwargs["metadata"] = self._safe(metadata)
        try:
            self.client.create_score(**kwargs)
        except Exception:
            _warn("Langfuse score failed")

    def flush(self) -> None:
        """刷新。"""
        self._safe_lifecycle("flush")

    def shutdown(self) -> None:
        """关闭。"""
        self._safe_lifecycle("shutdown")

    def _safe(self, value: object) -> Any:
        """执行 处理 safe 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        return sanitize_telemetry_value(
            value,
            sensitive_values=self.sensitive_values,
        )

    def _safe_exit(
        self,
        manager: AbstractContextManager[Any],
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """执行 处理 safe exit 的内部辅助逻辑。

        Args:
            manager: manager 参数。
            exc_type: exc_type 参数。
            exc: exc 参数。
            traceback: traceback 参数。
        """
        try:
            manager.__exit__(exc_type, exc, traceback)
        except Exception:
            _warn("Langfuse observation finish failed")

    def _safe_lifecycle(self, method_name: str) -> None:
        """执行 处理 safe lifecycle 的内部辅助逻辑。

        Args:
            method_name: method_name 参数。
        """
        try:
            getattr(self.client, method_name)()
        except Exception:
            _warn(f"Langfuse {method_name} failed")


def build_observability(
    settings: Settings,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> Observability:
    """构建 observability。

    Args:
        settings: settings 参数。
        client_factory: client_factory 参数。
    """
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return NoopObservability()
    try:
        if client_factory is None:
            from langfuse import Langfuse

            factory = Langfuse
        else:
            factory = client_factory
        client = cast(
            LangfuseClient,
            factory(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                base_url=settings.langfuse_base_url,
                environment=settings.app_env,
            ),
        )
    except Exception:
        _warn("Langfuse initialization failed")
        return NoopObservability()
    return LangfuseObservability(
        client,
        sensitive_values=(
            settings.langfuse_public_key,
            settings.langfuse_secret_key,
            settings.langfuse_base_url,
            settings.deepseek_api_key,
            settings.tavily_api_key,
        ),
    )


def _warn(message: str) -> None:
    """执行 处理 warn 的内部辅助逻辑。

    Args:
        message: message 参数。
    """
    logger.warning(message)
