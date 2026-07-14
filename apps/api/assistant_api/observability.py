from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from types import TracebackType
from typing import Any, Callable, Protocol, cast

from langfuse import Langfuse

from packages.observability import (
    NoopObservability,
    NoopObservation,
    Observability,
    Observation,
    ObservationType,
    ScoreType,
    ScoreValue,
    sanitize_telemetry_value,
)

from .config import Settings


logger = logging.getLogger(__name__)


class LangfuseObservationClient(Protocol):
    def update(self, **kwargs: Any) -> Any: ...


class LangfuseClient(Protocol):
    def start_as_current_observation(self, **kwargs: Any) -> AbstractContextManager[Any]: ...

    def create_score(self, **kwargs: Any) -> None: ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


class _LangfuseObservation:
    def __init__(
        self,
        client: LangfuseObservationClient,
        *,
        sensitive_values: tuple[str | None, ...],
    ) -> None:
        self.client = client
        self.sensitive_values = sensitive_values

    def update(
        self,
        *,
        output: object | None = None,
        error: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
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
        return sanitize_telemetry_value(
            value,
            sensitive_values=self.sensitive_values,
        )


class LangfuseObservability:
    def __init__(
        self,
        client: LangfuseClient,
        *,
        sensitive_values: tuple[str | None, ...] = (),
    ) -> None:
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
        self._safe_lifecycle("flush")

    def shutdown(self) -> None:
        self._safe_lifecycle("shutdown")

    def _safe(self, value: object) -> Any:
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
        try:
            manager.__exit__(exc_type, exc, traceback)
        except Exception:
            _warn("Langfuse observation finish failed")

    def _safe_lifecycle(self, method_name: str) -> None:
        try:
            getattr(self.client, method_name)()
        except Exception:
            _warn(f"Langfuse {method_name} failed")


def build_observability(
    settings: Settings,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> Observability:
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return NoopObservability()
    try:
        factory = client_factory or Langfuse
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
    logger.warning(message)
