from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import json
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from infrastructure.config import Settings
from app.main import create_app
from domain.models import Base, ModelLog, Task, TaskStatus, ToolLog, User


DEEPSEEK_URL = "https://deepseek.invalid/v1/chat/completions"
DEEPSEEK_API_KEY = "fake-deepseek-api-key"
PRIVATE_URL = "https://private.example.invalid/v1"
SECRET_TOKEN = "secret-token-value"


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/model-gateway.db",
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


@pytest.fixture
def client(sessionmaker: async_sessionmaker[AsyncSession]) -> TestClient:
    settings = Settings(
        database_url="sqlite+aiosqlite:///unused.db",
        deepseek_api_key=DEEPSEEK_API_KEY,
        deepseek_base_url="https://deepseek.invalid/v1",
        deepseek_light_model="deepseek-light-test",
        deepseek_standard_model="deepseek-standard-test",
        model_gateway_timeout_seconds=0.1,
        model_gateway_retry_attempts=2,
    )
    app = create_app(settings)
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    task_type: str = "plan",
) -> tuple[User, Task]:
    async with sessionmaker() as session:
        user = User(display_name=f"Model User {task_type}")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type=task_type,
            input_text="请处理这个任务",
            status=TaskStatus.PENDING,
        )
        session.add(task)
        await session.commit()
        return user, task


async def fetch_model_logs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> list[ModelLog]:
    async with sessionmaker() as session:
        result = await session.execute(select(ModelLog).order_by(ModelLog.created_at))
        return list(result.scalars())


async def fetch_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    task_id: str,
) -> Task:
    async with sessionmaker() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        return task


async def count_tool_logs(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker() as session:
        result = await session.execute(select(ToolLog))
        return len(list(result.scalars()))


def chat_request(
    *,
    user_id: str,
    task_id: str,
    task_type: str,
    model_class: str | None = None,
    messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "user_id": user_id,
        "task_id": task_id,
        "task_type": task_type,
        "messages": messages
        or [
            {"role": "system", "content": "你是个人 Agent 助手。"},
            {"role": "user", "content": "生成一个简短结果。"},
        ],
        "temperature": 0.2,
        "max_tokens": 512,
    }
    if model_class is not None:
        payload["model_class"] = model_class
    return payload


def deepseek_success(content: str = "模型输出") -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


def assert_no_sensitive_text(value: str) -> None:
    assert DEEPSEEK_API_KEY not in value
    assert PRIVATE_URL not in value
    assert SECRET_TOKEN not in value
    assert "Bearer " not in value


@pytest.mark.asyncio
@respx.mock
async def test_01_model_gateway_returns_unified_response(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_task(sessionmaker, task_type="router")
    route = respx.post(DEEPSEEK_URL).mock(
        return_value=httpx.Response(200, json=deepseek_success("轻量结果"))
    )

    response = client.post(
        "/internal/models/chat",
        json=chat_request(user_id=user.id, task_id=task.id, task_type="router"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "provider": "deepseek",
        "model": "deepseek-light-test",
        "content": "轻量结果",
        "usage": {"input_tokens": 11, "output_tokens": 7},
        "latency_ms": body["latency_ms"],
        "status": "succeeded",
    }
    assert isinstance(body["latency_ms"], int)
    assert route.calls.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_02_model_gateway_does_not_execute_downstream_work(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_task(sessionmaker, task_type="plan")
    respx.post(DEEPSEEK_URL).mock(
        return_value=httpx.Response(200, json=deepseek_success())
    )

    response = client.post(
        "/internal/models/chat",
        json=chat_request(user_id=user.id, task_id=task.id, task_type="plan"),
    )

    assert response.status_code == 200
    stored_task = await fetch_task(sessionmaker, task.id)
    assert stored_task.status == TaskStatus.PENDING
    assert stored_task.result_text is None
    assert stored_task.error_message is None
    assert await count_tool_logs(sessionmaker) == 0


@pytest.mark.asyncio
@respx.mock
async def test_03_malformed_request_is_rejected_before_provider_call(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_task(sessionmaker, task_type="router")
    route = respx.post(DEEPSEEK_URL).mock(
        return_value=httpx.Response(200, json=deepseek_success())
    )

    response = client.post(
        "/internal/models/chat",
        json=chat_request(
            user_id=user.id,
            task_id=task.id,
            task_type="router",
            messages=[{"role": "tool", "content": "bad role"}],
        ),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert route.calls.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_04_light_task_types_default_to_light_model(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    route = respx.post(DEEPSEEK_URL).mock(
        return_value=httpx.Response(200, json=deepseek_success())
    )

    for task_type in ["router", "memory_extract", "status_summary", "card_render"]:
        user, task = await create_task(sessionmaker, task_type=task_type)
        response = client.post(
            "/internal/models/chat",
            json=chat_request(user_id=user.id, task_id=task.id, task_type=task_type),
        )
        assert response.status_code == 200

    assert route.calls.call_count == 4
    for call in route.calls:
        assert call.request.headers["authorization"] == f"Bearer {DEEPSEEK_API_KEY}"
        assert '"model":"deepseek-light-test"' in call.request.content.decode()


@pytest.mark.asyncio
@respx.mock
async def test_05_standard_task_types_default_to_standard_model(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    route = respx.post(DEEPSEEK_URL).mock(
        return_value=httpx.Response(200, json=deepseek_success())
    )

    for task_type in ["plan", "learn", "daily", "office_text", "research_report"]:
        user, task = await create_task(sessionmaker, task_type=task_type)
        response = client.post(
            "/internal/models/chat",
            json=chat_request(user_id=user.id, task_id=task.id, task_type=task_type),
        )
        assert response.status_code == 200

    assert route.calls.call_count == 5
    for call in route.calls:
        assert '"model":"deepseek-standard-test"' in call.request.content.decode()


@pytest.mark.asyncio
@respx.mock
async def test_06_explicit_model_class_overrides_default_route(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_task(sessionmaker, task_type="plan")
    route = respx.post(DEEPSEEK_URL).mock(
        return_value=httpx.Response(200, json=deepseek_success())
    )

    response = client.post(
        "/internal/models/chat",
        json=chat_request(
            user_id=user.id,
            task_id=task.id,
            task_type="plan",
            model_class="light",
        ),
    )

    assert response.status_code == 200
    assert '"model":"deepseek-light-test"' in route.calls.last.request.content.decode()


@pytest.mark.asyncio
@respx.mock
async def test_07_unsupported_routing_inputs_return_stable_errors(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    route = respx.post(DEEPSEEK_URL).mock(
        return_value=httpx.Response(200, json=deepseek_success())
    )
    cases = [
        ("unknown_type", None, "model_gateway_validation_error"),
        ("plan", "tiny", "model_gateway_validation_error"),
        ("plan", "complex", "model_gateway_unsupported_model"),
        ("coding_plan", None, "model_gateway_unsupported_model"),
    ]

    for task_type, model_class, expected_code in cases:
        user, task = await create_task(sessionmaker, task_type=task_type)
        response = client.post(
            "/internal/models/chat",
            json=chat_request(
                user_id=user.id,
                task_id=task.id,
                task_type=task_type,
                model_class=model_class,
            ),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == expected_code

    assert route.calls.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_08_response_normalizes_usage_for_light_and_standard_successes(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    respx.post(DEEPSEEK_URL).mock(
        return_value=httpx.Response(200, json=deepseek_success("统一输出"))
    )

    for task_type, expected_model in [
        ("router", "deepseek-light-test"),
        ("plan", "deepseek-standard-test"),
    ]:
        user, task = await create_task(sessionmaker, task_type=task_type)
        response = client.post(
            "/internal/models/chat",
            json=chat_request(user_id=user.id, task_id=task.id, task_type=task_type),
        )
        assert response.status_code == 200
        assert response.json()["model"] == expected_model
        assert response.json()["usage"] == {"input_tokens": 11, "output_tokens": 7}


@pytest.mark.asyncio
@respx.mock
async def test_09_transient_provider_failure_is_retried(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_task(sessionmaker, task_type="plan")
    route = respx.post(DEEPSEEK_URL).mock(
        side_effect=[
            httpx.Response(503, json={"error": "temporary"}),
            httpx.Response(200, json=deepseek_success("重试成功")),
        ]
    )

    response = client.post(
        "/internal/models/chat",
        json=chat_request(user_id=user.id, task_id=task.id, task_type="plan"),
    )

    assert response.status_code == 200
    assert response.json()["content"] == "重试成功"
    assert route.calls.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_10_timeout_is_mapped_to_gateway_error_and_logged(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_task(sessionmaker, task_type="plan")
    route = respx.post(DEEPSEEK_URL).mock(
        side_effect=[
            httpx.TimeoutException(
                f"timeout Bearer {DEEPSEEK_API_KEY} token={SECRET_TOKEN}"
            ),
            httpx.TimeoutException("timeout again"),
        ]
    )

    response = client.post(
        "/internal/models/chat",
        json=chat_request(user_id=user.id, task_id=task.id, task_type="plan"),
    )

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "model_gateway_timeout"
    assert route.calls.call_count == 2
    assert_no_sensitive_text(response.text)
    logs = await fetch_model_logs(sessionmaker)
    assert len(logs) == 1
    assert logs[0].error_message is not None
    assert_no_sensitive_text(logs[0].error_message)


@pytest.mark.asyncio
@respx.mock
async def test_11_provider_error_is_mapped_and_success_or_failure_is_logged(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    success_user, success_task = await create_task(sessionmaker, task_type="router")
    failure_user, failure_task = await create_task(sessionmaker, task_type="plan")
    route = respx.post(DEEPSEEK_URL).mock(
        side_effect=[
            httpx.Response(200, json=deepseek_success("成功")),
            httpx.Response(500, json={"error": f"bad {PRIVATE_URL}"}),
            httpx.Response(500, json={"error": "bad again"}),
        ]
    )

    success = client.post(
        "/internal/models/chat",
        json=chat_request(
            user_id=success_user.id,
            task_id=success_task.id,
            task_type="router",
        ),
    )
    failure = client.post(
        "/internal/models/chat",
        json=chat_request(
            user_id=failure_user.id,
            task_id=failure_task.id,
            task_type="plan",
        ),
    )

    assert success.status_code == 200
    assert failure.status_code == 502
    assert failure.json()["error"]["code"] == "model_gateway_provider_error"
    assert route.calls.call_count == 3
    logs = await fetch_model_logs(sessionmaker)
    assert len(logs) == 2
    assert logs[0].response_text is not None
    assert logs[0].error_message is None
    assert logs[1].response_text is None
    assert logs[1].error_message is not None


@pytest.mark.asyncio
@respx.mock
async def test_12_sensitive_values_are_absent_from_errors_and_model_logs(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_task(sessionmaker, task_type="plan")
    respx.post(DEEPSEEK_URL).mock(
        side_effect=[
            httpx.Response(
                500,
                json={
                    "error": (
                        f"Bearer {DEEPSEEK_API_KEY} token={SECRET_TOKEN} {PRIVATE_URL}"
                    )
                },
            ),
            httpx.Response(500, json={"error": "still failing"}),
        ]
    )

    response = client.post(
        "/internal/models/chat",
        json=chat_request(user_id=user.id, task_id=task.id, task_type="plan"),
    )

    assert response.status_code == 502
    assert_no_sensitive_text(response.text)
    logs = await fetch_model_logs(sessionmaker)
    assert len(logs) == 1
    for value in [logs[0].request_text, logs[0].response_text, logs[0].error_message]:
        if value is not None:
            assert_no_sensitive_text(value)


@pytest.mark.asyncio
async def test_task_event_stream_is_owner_scoped_and_resumable(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from domain.task_events import TaskEventPublisher

    user, task = await create_task(sessionmaker, task_type="plan")
    async with sessionmaker() as session:
        stored = await session.get(Task, task.id)
        assert stored is not None
        stored.status = TaskStatus.SUCCESS.value
        await session.commit()
    publisher = TaskEventPublisher(sessionmaker)
    await publisher.publish(
        task_id=task.id,
        user_id=user.id,
        event_type="plan",
        payload={"steps": ["先返回计划"]},
    )
    await publisher.publish(
        task_id=task.id,
        user_id=user.id,
        event_type="status",
        payload={"status": "success"},
    )

    response = client.get(
        f"/api/tasks/{task.id}/events/stream",
        params={"user_id": user.id, "after": 1},
    )
    denied = client.get(
        f"/api/tasks/{task.id}/events/stream",
        params={"user_id": "other-user"},
    )

    assert response.status_code == 200
    records = [json.loads(line) for line in response.text.splitlines()]
    assert [(item["sequence"], item["type"]) for item in records] == [(2, "status")]
    assert denied.status_code == 404
    assert "先返回计划" not in denied.text


@pytest.mark.asyncio
async def test_task_event_stream_ends_for_cancelled_task(
    client: TestClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user, task = await create_task(sessionmaker, task_type="plan")
    async with sessionmaker() as session:
        stored = await session.get(Task, task.id)
        assert stored is not None
        stored.status = TaskStatus.CANCELLED.value
        await session.commit()

    response = client.get(
        f"/api/tasks/{task.id}/events/stream",
        params={"user_id": user.id},
    )

    assert response.status_code == 200
    assert response.text == ""
