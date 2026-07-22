from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from infrastructure.settings.config import Settings
from app.main import create_app
from infrastructure.telemetry.observability import (
    LangfuseObservability,
    build_observability,
)
from evaluation import run_langfuse_experiment
from evaluation.loader import DatasetSecurityError
from infrastructure.telemetry.observability import NoopObservability, sanitize_telemetry_value


class FakeSdkObservation:
    def __init__(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        self.events = events

    def update(self, **kwargs: Any) -> None:
        self.events.append(("update", kwargs))


class FakeObservationManager(AbstractContextManager[FakeSdkObservation]):
    def __init__(self, client: "FakeLangfuseClient") -> None:
        self.client = client

    def __enter__(self) -> FakeSdkObservation:
        return FakeSdkObservation(self.client.events)

    def __exit__(self, *args: Any) -> None:
        self.client.events.append(("finish", {}))


class FakeLangfuseClient:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.flush_count = 0
        self.shutdown_count = 0

    def start_as_current_observation(self, **kwargs: Any) -> FakeObservationManager:
        self.events.append(("start", kwargs))
        return FakeObservationManager(self)

    def create_score(self, **kwargs: Any) -> None:
        self.events.append(("score", kwargs))

    def flush(self) -> None:
        self.flush_count += 1

    def shutdown(self) -> None:
        self.shutdown_count += 1


class FailingLangfuseClient(FakeLangfuseClient):
    def start_as_current_observation(self, **kwargs: Any) -> FakeObservationManager:
        del kwargs
        raise RuntimeError("sdk unavailable secret-value")

    def create_score(self, **kwargs: Any) -> None:
        del kwargs
        raise RuntimeError("sdk unavailable secret-value")

    def flush(self) -> None:
        raise RuntimeError("sdk unavailable secret-value")

    def shutdown(self) -> None:
        raise RuntimeError("sdk unavailable secret-value")


def test_01_missing_or_partial_credentials_use_noop_without_building_client() -> None:
    calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> FakeLangfuseClient:
        calls.append(kwargs)
        return FakeLangfuseClient()

    assert isinstance(
        build_observability(Settings(), client_factory=factory), NoopObservability
    )
    assert isinstance(
        build_observability(
            Settings(langfuse_public_key="public-only"),
            client_factory=factory,
        ),
        NoopObservability,
    )
    assert calls == []


def test_02_complete_credentials_build_adapter_without_exposing_keys() -> None:
    calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> FakeLangfuseClient:
        calls.append(kwargs)
        return FakeLangfuseClient()

    observability = build_observability(
        Settings(
            langfuse_public_key="public-placeholder",
            langfuse_secret_key="secret-placeholder",
            langfuse_base_url="https://langfuse.invalid",
        ),
        client_factory=factory,
    )

    assert isinstance(observability, LangfuseObservability)
    assert calls == [
        {
            "public_key": "public-placeholder",
            "secret_key": "secret-placeholder",
            "base_url": "https://langfuse.invalid",
            "environment": "local",
        }
    ]
    assert "secret-placeholder" not in repr(observability)


def test_03_observation_recursively_sanitizes_and_bounds_payloads() -> None:
    client = FakeLangfuseClient()
    observability = LangfuseObservability(
        client,
        sensitive_values=("secret-value",),
    )

    with observability.observe(
        "agent.task",
        as_type="agent",
        input={"nested": {"token": "secret-value"}},
        metadata={"task_id": "task-1"},
    ) as observation:
        observation.update(
            output={"answer": "Bearer secret-value"},
            metadata={"items": list(range(30))},
        )

    serialized = repr(client.events)
    assert "agent.task" in serialized
    assert "task-1" in serialized
    assert "secret-value" not in serialized
    assert "[REDACTED]" in serialized
    assert "[truncated]" in serialized


def test_04_sdk_failures_are_best_effort_and_business_errors_still_raise() -> None:
    observability = LangfuseObservability(
        FailingLangfuseClient(),
        sensitive_values=("secret-value",),
    )

    with observability.observe("agent.task") as observation:
        observation.update(output="business result")
    observability.score(name="correct", value=True, trace_id="trace-1")
    observability.flush()
    observability.shutdown()

    with pytest.raises(ValueError, match="business failed"):
        with LangfuseObservability(FakeLangfuseClient()).observe("agent.task"):
            raise ValueError("business failed")


def test_05_score_and_lifecycle_use_public_sdk_boundary() -> None:
    client = FakeLangfuseClient()
    observability = LangfuseObservability(client)

    observability.score(
        name="quality",
        value=1.0,
        trace_id="trace-1",
        data_type="NUMERIC",
        metadata={"task_id": "task-1"},
    )
    observability.flush()
    observability.shutdown()

    assert client.events == [
        (
            "score",
            {
                "name": "quality",
                "value": 1.0,
                "trace_id": "trace-1",
                "data_type": "NUMERIC",
                "metadata": {"task_id": "task-1"},
            },
        )
    ]
    assert client.flush_count == client.shutdown_count == 1


def test_06_sanitizer_handles_depth_and_non_json_values() -> None:
    safe = sanitize_telemetry_value(
        {"value": object(), "deep": {"a": {"b": {"c": {"d": "hidden"}}}}}
    )

    assert isinstance(safe, dict)
    assert safe["deep"]["a"]["b"]["c"] == "[truncated]"


def test_07_experiment_adapter_passes_real_task_callable_to_client() -> None:
    calls: list[dict[str, Any]] = []
    executions: list[str] = []

    class ExperimentClient:
        def run_experiment(self, **kwargs: Any) -> str:
            calls.append(kwargs)
            kwargs["task"](item=kwargs["data"][0])
            return "experiment-result"

    def real_task(*, item: dict[str, Any]) -> str:
        executions.append(item["metadata"]["case_id"])
        return f"executed:{item['input']}"

    result = run_langfuse_experiment(
        client=ExperimentClient(),
        name="agent-core-regression",
        items=[
            {
                "id": "plan-basic",
                "input": "安排今天",
                "expected_output": "计划",
                "metadata": {"task_type": "plan"},
            }
        ],
        task=real_task,
        metadata={"version": "v3-08"},
    )

    assert result == "experiment-result"
    assert calls[0]["task"] is real_task
    assert calls[0]["data"][0]["input"] == "安排今天"
    assert calls[0]["data"][0]["metadata"]["case_id"] == "plan-basic"
    assert "id" not in calls[0]["data"][0]
    assert "actual_output" not in calls[0]["data"][0]
    assert executions == ["plan-basic"]


def test_08_experiment_rejects_private_or_sensitive_items_before_client() -> None:
    class ExperimentClient:
        def run_experiment(self, **kwargs: Any) -> None:
            raise AssertionError(f"client must not be called: {kwargs}")

    with pytest.raises(DatasetSecurityError) as exc_info:
        run_langfuse_experiment(
            client=ExperimentClient(),
            name="unsafe",
            items=[{"id": "unsafe-case", "input": "http://127.0.0.1/private"}],
            task=lambda **_kwargs: None,
        )

    assert "unsafe-case" in str(exc_info.value)
    assert "127.0.0.1" not in str(exc_info.value)


def test_09_fastapi_lifespan_shuts_down_injected_observability() -> None:
    client = FakeLangfuseClient()
    observability = LangfuseObservability(client)
    app = create_app(
        Settings(database_url="sqlite+aiosqlite:///unused.db"),
        observability=observability,
    )

    with TestClient(app):
        assert app.state.observability is observability

    assert client.shutdown_count == 1
