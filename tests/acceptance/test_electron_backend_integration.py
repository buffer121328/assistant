from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from application.task_execution.events import TaskEventRepository
from domain.models import Approval, Base, Task, User
from infrastructure.settings.config import Settings


ROOT = Path(__file__).resolve().parents[2]
ALLOWED_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


def settings_without_env(**values: object) -> Settings:
    """Call Pydantic Settings' documented runtime constructor override safely."""
    factory = cast(Callable[..., Settings], Settings)
    return factory(_env_file=None, **values)


@pytest_asyncio.fixture
async def db_sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/electron.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_electron_vite_origins_are_allowed_without_wildcard_cors() -> None:
    app = create_app(settings_without_env(redis_url="redis://placeholder"))
    with TestClient(app) as client:
        for origin in ALLOWED_ORIGINS:
            response = client.options(
                "/local/config",
                headers={
                    "origin": origin,
                    "access-control-request-method": "GET",
                },
            )
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin

        denied = client.options(
            "/local/config",
            headers={
                "origin": "https://untrusted.example",
                "access-control-request-method": "GET",
            },
        )

    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_explicit_local_auth_remains_fail_closed_with_cors_enabled() -> None:
    app = create_app(
        settings_without_env(
            local_api_auth_required=True,
            local_api_token=SecretStr("test-local-token"),
            redis_url="redis://placeholder",
        )
    )
    headers = {"origin": ALLOWED_ORIGINS[0]}
    with TestClient(app) as client:
        missing = client.get("/local/config", headers=headers)
        allowed = client.get(
            "/local/config",
            headers={
                **headers,
                "authorization": "Bearer test-local-token",
            },
        )

    assert missing.status_code == 401
    assert missing.headers["access-control-allow-origin"] == ALLOWED_ORIGINS[0]
    assert allowed.status_code == 200


async def _create_change_approval(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    async with sessionmaker() as session:
        user = User(display_name="Electron User")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="local",
            task_type="plan",
            input_text="apply governed change",
            status="waiting_approval",
        )
        session.add(task)
        await session.flush()
        session.add(
            Approval(
                task_id=task.id,
                status="pending",
                tool_name="agent.change.apply",
                approval_type="change",
                subject="proposal:change-1",
                request_summary="Apply the reviewed proposal.",
            )
        )
        await session.commit()
        return user.id, task.id


async def _create_streaming_task(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    async with sessionmaker() as session:
        user = User(display_name="Streaming User")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="local",
            task_type="plan",
            input_text="stream events",
            status="running",
        )
        session.add(task)
        await session.flush()
        await TaskEventRepository(session).append(
            task_id=task.id,
            user_id=user.id,
            event_type="task.started",
            payload={"message": "started"},
        )
        await session.commit()
        return user.id, task.id


@pytest.mark.asyncio
async def test_local_approval_api_serializes_change_type(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id, task_id = await _create_change_approval(db_sessionmaker)
    app = create_app(settings_without_env(redis_url="redis://placeholder"))
    app.state.db_sessionmaker = db_sessionmaker

    with TestClient(app) as client:
        response = client.get(
            f"/local/tasks/{task_id}/approvals",
            params={"user_id": user_id},
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["approval_type"] == "change"


@pytest.mark.asyncio
async def test_local_event_websocket_allows_only_documented_browser_origins(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id, task_id = await _create_streaming_task(db_sessionmaker)
    app = create_app(settings_without_env(redis_url="redis://placeholder"))
    app.state.db_sessionmaker = db_sessionmaker
    path = f"/local/tasks/{task_id}/events/stream?user_id={user_id}"

    with TestClient(app) as client:
        with client.websocket_connect(
            path,
            headers={"origin": ALLOWED_ORIGINS[0]},
        ) as websocket:
            assert websocket.receive_json()["type"] == "task.started"

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                path,
                headers={"origin": "https://untrusted.example"},
            ):
                pass


@pytest.mark.asyncio
async def test_local_event_websocket_honors_explicit_authentication(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id, task_id = await _create_streaming_task(db_sessionmaker)
    app = create_app(
        settings_without_env(
            local_api_auth_required=True,
            local_api_token=SecretStr("test-local-token"),
            redis_url="redis://placeholder",
        )
    )
    app.state.db_sessionmaker = db_sessionmaker
    path = f"/local/tasks/{task_id}/events/stream?user_id={user_id}"

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                path,
                headers={"origin": ALLOWED_ORIGINS[0]},
            ):
                pass

        with client.websocket_connect(
            path,
            headers={
                "origin": ALLOWED_ORIGINS[0],
                "authorization": "Bearer test-local-token",
            },
        ) as websocket:
            assert websocket.receive_json()["type"] == "task.started"


def test_electron_build_configuration_matches_electron_vite_output() -> None:
    desktop_root = ROOT / "frontend" / "desktop"
    package = json.loads((desktop_root / "package.json").read_text())
    builder = json.loads((desktop_root / "electron-builder.json").read_text())
    main_source = (desktop_root / "src" / "main" / "index.ts").read_text()

    assert package["main"] == "out/main/index.js"
    assert "out/**" in builder["files"]
    assert "dist/**" not in builder["files"]
    assert 'join(__dirname, "../preload/index.mjs")' in main_source


def test_local_startup_templates_match_electron_auth_capability() -> None:
    env_example = (ROOT / ".env.example").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    readme = (ROOT / "README.md").read_text()
    guide = (ROOT / "项目全量启动指南.txt").read_text()
    desktop_main = (ROOT / "frontend" / "desktop" / "src" / "main" / "index.ts").read_text()
    desktop_renderer = (ROOT / "frontend" / "desktop" / "src" / "renderer" / "App.tsx").read_text()

    assert "LOCAL_API_AUTH_REQUIRED=false" in env_example
    assert "ASSISTANT_API_HOST_PORT=18080" in env_example
    assert "LOCAL_API_AUTH_REQUIRED: ${LOCAL_API_AUTH_REQUIRED:-false}" in compose
    assert '"127.0.0.1:${ASSISTANT_API_HOST_PORT:-18080}:8000"' in compose
    assert 'apiBaseUrl: "http://127.0.0.1:18080"' in desktop_main
    assert 'apiBaseUrl: "http://127.0.0.1:18080"' in desktop_renderer
    assert "http://127.0.0.1:18080" in readme
    assert "LOCAL_API_AUTH_REQUIRED=false" in readme
    assert "LOCAL_API_AUTH_REQUIRED=false" in guide
    assert "本机开发链路已对应" in guide
    assert "修复以上问题前" not in guide
