from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from assistant_api.config import Settings
from assistant_api.main import create_app
from assistant_api.models import Base, PlatformAccount, Task, TaskStatus, User

WEBHOOK_PATH = "/api/webhooks/feishu"
FEISHU_TOKEN = "test-feishu-token"
FEISHU_SIGNING_SECRET = "test-feishu-signing-secret"


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'feishu.db'}"
    engine = create_async_engine(database_url)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
def client(sessionmaker: async_sessionmaker) -> TestClient:
    settings = Settings(
        database_url="sqlite+aiosqlite:///test.db",
        feishu_webhook_verification_token=FEISHU_TOKEN,
        feishu_webhook_signing_secret=FEISHU_SIGNING_SECRET,
    )
    app = create_app(settings)
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


def signed_body_and_headers(
    payload: dict[str, object],
    *,
    signing_secret: str = FEISHU_SIGNING_SECRET,
    signature_override: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = "1700000000"
    digest = hmac.new(
        signing_secret.encode(),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    ).digest()
    signature = signature_override or base64.b64encode(digest).decode()
    return body, {
        "content-type": "application/json",
        "x-feishu-request-timestamp": timestamp,
        "x-feishu-signature": signature,
    }


def post_feishu(
    client: TestClient,
    payload: dict[str, object],
    *,
    signature_override: str | None = None,
) -> Response:
    body, headers = signed_body_and_headers(
        payload,
        signature_override=signature_override,
    )
    return client.post(WEBHOOK_PATH, content=body, headers=headers)


def challenge_payload(
    *,
    token: str = FEISHU_TOKEN,
    challenge: str = "challenge-value",
) -> dict[str, object]:
    return {
        "type": "url_verification",
        "token": token,
        "challenge": challenge,
    }


def message_payload(
    text: str,
    *,
    token: str = FEISHU_TOKEN,
    message_id: str = "om_test_message",
    platform_user_id: str = "ou_test_user",
    chat_id: str = "oc_test_chat",
    message_type: str = "text",
) -> dict[str, object]:
    content = json.dumps({"text": text}, ensure_ascii=False)
    return {
        "schema": "2.0",
        "token": token,
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": platform_user_id}},
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "message_type": message_type,
                "content": content,
            },
        },
    }


async def create_bound_user(
    sessionmaker: async_sessionmaker,
    *,
    platform_user_id: str = "ou_test_user",
) -> str:
    async with sessionmaker() as session:
        user = User(display_name="Feishu User")
        session.add(user)
        await session.flush()
        account = PlatformAccount(
            user_id=user.id,
            platform="feishu",
            platform_user_id=platform_user_id,
        )
        session.add(account)
        await session.commit()
        return user.id


async def list_tasks(sessionmaker: async_sessionmaker) -> list[Task]:
    async with sessionmaker() as session:
        result = await session.scalars(select(Task).order_by(Task.created_at))
        return list(result.all())


async def list_users(sessionmaker: async_sessionmaker) -> list[User]:
    async with sessionmaker() as session:
        result = await session.scalars(select(User).order_by(User.created_at))
        return list(result.all())


@pytest.mark.asyncio
async def test_01_challenge_returns_challenge_without_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    response = post_feishu(client, challenge_payload(challenge="verify-me"))

    assert response.status_code == 200
    assert response.json() == {"challenge": "verify-me"}
    assert await list_tasks(sessionmaker) == []


@pytest.mark.asyncio
async def test_02_invalid_signature_returns_error_without_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    response = post_feishu(
        client,
        challenge_payload(),
        signature_override="invalid-signature",
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "feishu_invalid_signature"
    assert await list_tasks(sessionmaker) == []


@pytest.mark.asyncio
async def test_03_invalid_token_returns_error_without_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    response = post_feishu(client, challenge_payload(token="wrong-token"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "feishu_invalid_token"
    assert await list_tasks(sessionmaker) == []


@pytest.mark.asyncio
async def test_04_unrecognized_format_returns_error_without_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    response = post_feishu(client, {"type": "unknown", "token": FEISHU_TOKEN})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "feishu_unrecognized_request"
    assert await list_tasks(sessionmaker) == []


@pytest.mark.asyncio
async def test_05_valid_text_event_returns_normalized_message(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)
    response = post_feishu(
        client,
        message_payload(
            "/plan 做一个个人助手",
            message_id="om_normalize",
            platform_user_id="ou_test_user",
            chat_id="oc_chat_1",
        ),
    )

    assert response.status_code == 200
    assert response.json()["message"] == {
        "platform": "feishu",
        "platform_user_id": "ou_test_user",
        "chat_id": "oc_chat_1",
        "text": "/plan 做一个个人助手",
        "message_id": "om_normalize",
    }


@pytest.mark.asyncio
async def test_06_bound_plan_command_creates_one_pending_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    user_id = await create_bound_user(sessionmaker)
    response = post_feishu(
        client,
        message_payload("/plan 做一个个人助手", message_id="om_plan"),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "task_created"
    tasks = await list_tasks(sessionmaker)
    assert len(tasks) == 1
    assert tasks[0].user_id == user_id
    assert tasks[0].task_type == "plan"
    assert tasks[0].input_text == "/plan 做一个个人助手"
    assert tasks[0].status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_07_every_mvp_command_maps_to_task_type(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)
    expected = {
        "/plan": "plan",
        "/learn": "learn",
        "/daily": "daily",
        "/office": "office",
        "/memory": "memory",
        "/status": "status",
    }

    for index, command in enumerate(expected, start=1):
        response = post_feishu(
            client,
            message_payload(f"{command} 任务内容", message_id=f"om_command_{index}"),
        )
        assert response.status_code == 200
        assert response.json()["reason"] == "task_created"

    tasks = await list_tasks(sessionmaker)
    assert [task.task_type for task in tasks] == list(expected.values())


@pytest.mark.asyncio
async def test_08_duplicate_message_acknowledged_without_second_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)
    payload = message_payload("/plan 去重测试", message_id="om_duplicate")

    first_response = post_feishu(client, payload)
    second_response = post_feishu(client, payload)

    assert first_response.status_code == 200
    assert first_response.json()["reason"] == "task_created"
    assert second_response.status_code == 200
    assert second_response.json()["reason"] == "duplicate_message"
    assert len(await list_tasks(sessionmaker)) == 1


@pytest.mark.asyncio
async def test_09_unknown_command_acknowledged_without_executable_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)
    response = post_feishu(
        client,
        message_payload("/unknown 任务内容", message_id="om_unknown"),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "unknown_command"
    assert await list_tasks(sessionmaker) == []


@pytest.mark.asyncio
async def test_10_unbound_user_acknowledged_without_task_or_user_creation(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    response = post_feishu(
        client,
        message_payload("/plan 未绑定用户", message_id="om_unbound"),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "unbound_user"
    assert await list_tasks(sessionmaker) == []
    assert await list_users(sessionmaker) == []


@pytest.mark.asyncio
async def test_11_non_text_message_acknowledged_without_task(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)
    response = post_feishu(
        client,
        message_payload(
            "",
            message_id="om_non_text",
            message_type="image",
        ),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "non_text_message"
    assert await list_tasks(sessionmaker) == []


@pytest.mark.asyncio
async def test_12_task_is_only_persisted_pending_without_downstream_execution(
    client: TestClient,
    sessionmaker: async_sessionmaker,
) -> None:
    await create_bound_user(sessionmaker)
    response = post_feishu(
        client,
        message_payload("/learn 只入库不执行", message_id="om_pending_only"),
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "task_created"
    tasks = await list_tasks(sessionmaker)
    assert len(tasks) == 1
    assert tasks[0].task_type == "learn"
    assert tasks[0].status == TaskStatus.PENDING
    assert tasks[0].result_text is None
    assert tasks[0].error_message is None
