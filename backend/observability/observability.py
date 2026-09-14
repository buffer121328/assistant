from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from types import TracebackType
from typing import Any, Callable, Literal, Protocol, cast

from infrastructure.settings.config import Settings
from observability.callbacks import build_langgraph_callback_handler

ObservationType = Literal["span", "agent", "tool", "generation", "evaluator"]
ScoreValue = float | str | bool
ScoreType = Literal["NUMERIC", "CATEGORICAL", "BOOLEAN"]


class LangfuseObservationClient(Protocol):
    """定义当前组件的接口契约。"""

    def update(self, **kwargs: Any) -> None:
        """Update the active observation.

        Args:
            kwargs: 用于执行当前操作的 kwargs 参数。
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
        from infrastructure.telemetry.observability import _safe_identifier

        self.client = client
        self.sensitive_values = sensitive_values
        self._trace_id = _safe_identifier(getattr(client, "trace_id", None))
        self._observation_id = _safe_identifier(
            getattr(client, "observation_id", None) or getattr(client, "id", None)
        )

    @property
    def trace_id(self) -> str | None:
        """执行当前组件定义的业务处理逻辑。"""
        return self._trace_id

    @property
    def observation_id(self) -> str | None:
        """执行当前组件定义的业务处理逻辑。"""
        return self._observation_id

    def update(
        self,
        *,
        output: object | None = None,
        error: object | None = None,
        metadata: Mapping[str, object] | None = None,
        usage_details: Mapping[str, object] | None = None,
        cost_details: Mapping[str, object] | None = None,
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
        if usage_details is not None:
            payload["usage_details"] = self._safe(usage_details)
        if cost_details is not None:
            payload["cost_details"] = self._safe(cost_details)
        if error is not None:
            payload["level"] = "ERROR"
            payload["status_message"] = self._safe(error)
        if not payload:
            return
        try:
            self.client.update(**payload)
        except Exception:
            from infrastructure.telemetry.observability import _warn

            _warn("Langfuse observation update failed")

    def _safe(self, value: object) -> Any:
        """执行 处理 safe 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        from infrastructure.telemetry.observability import sanitize_telemetry_value

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
        langgraph_callback_factory: Callable[[], Any | None] | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            client: client 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.client = client
        self.sensitive_values = sensitive_values
        self._langgraph_callback_factory = langgraph_callback_factory
        self._langgraph_callbacks: tuple[Any, ...] | None = None

    @contextmanager
    def observe(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        input: object | None = None,
        metadata: Mapping[str, object] | None = None,
        model: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: Sequence[str] | None = None,
        version: str | None = None,
        model_parameters: Mapping[str, object] | None = None,
        prompt: Mapping[str, object] | None = None,
    ) -> Iterator[Any]:
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
        if user_id is not None:
            kwargs["user_id"] = self._safe(user_id)
        if session_id is not None:
            kwargs["session_id"] = self._safe(session_id)
        if tags is not None:
            kwargs["tags"] = self._safe(tuple(tags))
        if version is not None:
            kwargs["version"] = self._safe(version)
        if model_parameters is not None:
            kwargs["model_parameters"] = self._safe(model_parameters)
        if prompt is not None:
            kwargs["prompt"] = self._safe(prompt)
        try:
            manager = self.client.start_as_current_observation(**kwargs)
            sdk_observation = manager.__enter__()
        except Exception:
            from infrastructure.telemetry.observability import NoopObservation, _warn

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
            from infrastructure.telemetry.observability import _warn

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
        from infrastructure.telemetry.observability import sanitize_telemetry_value

        return sanitize_telemetry_value(
            value,
            sensitive_values=self.sensitive_values,
        )

    def langgraph_callbacks(self) -> tuple[Any, ...]:
        """执行当前组件定义的业务处理逻辑。"""
        if self._langgraph_callbacks is not None:
            return self._langgraph_callbacks
        if self._langgraph_callback_factory is None:
            self._langgraph_callbacks = ()
            return self._langgraph_callbacks
        try:
            callback = self._langgraph_callback_factory()
        except Exception:
            from infrastructure.telemetry.observability import _warn

            _warn("Langfuse LangGraph callback initialization failed")
            callback = None
        self._langgraph_callbacks = (callback,) if callback is not None else ()
        return self._langgraph_callbacks

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
            from infrastructure.telemetry.observability import _warn

            _warn("Langfuse observation finish failed")

    def _safe_lifecycle(self, method_name: str) -> None:
        """执行 处理 safe lifecycle 的内部辅助逻辑。

        Args:
            method_name: method_name 参数。
        """
        try:
            getattr(self.client, method_name)()
        except Exception:
            from infrastructure.telemetry.observability import _warn

            _warn(f"Langfuse {method_name} failed")


def build_langfuse_observability(
    settings: Settings,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> Any:
    """构建 observability。

    Args:
        settings: settings 参数。
        client_factory: client_factory 参数。
    """
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        from infrastructure.telemetry.observability import NoopObservability

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
        from infrastructure.telemetry.observability import NoopObservability, _warn

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
        langgraph_callback_factory=lambda: build_langgraph_callback_handler(settings),
    )
