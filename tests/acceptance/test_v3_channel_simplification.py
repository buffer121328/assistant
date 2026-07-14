from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from assistant_api.config import Settings
from assistant_api.main import create_app


ROOT = Path(__file__).parents[2]


def test_removed_http_entrypoints_return_standard_not_found() -> None:
    app = create_app()

    with TestClient(app) as client:
        responses = [
            client.get("/app"),
            client.get("/app/"),
            client.post("/api/webhooks/feishu", json={}),
        ]

    assert all(response.status_code == 404 for response in responses)
    assert all(
        response.json()
        == {"error": {"code": "not_found", "message": "Resource not found"}}
        for response in responses
    )


def test_runtime_settings_only_expose_supported_channel_configuration() -> None:
    fields = Settings.model_fields

    assert "langbot_webhook_secret" in fields
    assert not any(field.startswith("feishu_") for field in fields)


def test_langbot_command_mapping_has_no_removed_channel_import() -> None:
    langbot_source = (ROOT / "apps/api/assistant_api/langbot.py").read_text(
        encoding="utf-8"
    )
    command_source = (ROOT / "apps/api/assistant_api/commands.py").read_text(
        encoding="utf-8"
    )
    imported_modules = {
        alias.name
        for source in (langbot_source, command_source)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "feishu" not in " ".join(imported_modules).lower()


def test_removed_product_source_and_docker_build_are_absent() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert not (ROOT / "frontend").exists()
    assert not (ROOT / "cli").exists()
    assert "node:" not in dockerfile
    assert "frontend" not in dockerfile
    assert "COPY cli" not in dockerfile
