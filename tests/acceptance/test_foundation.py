from __future__ import annotations

from fastapi.testclient import TestClient

from assistant_api.config import load_settings
from assistant_api.errors import AppError
from assistant_api.logging import configure_logging
from assistant_api.main import create_app


EXTERNAL_ENV_VARS = (
    "DATABASE_URL",
    "REDIS_URL",
    "SENTRY_DSN",
    "DEEPSEEK_API_KEY",
    "DIFY_API_KEY",
    "TAVILY_API_KEY",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_WEBHOOK_VERIFICATION_TOKEN",
    "FEISHU_WEBHOOK_SIGNING_SECRET",
)


def test_app_starts_with_default_local_configuration(monkeypatch) -> None:
    for key in EXTERNAL_ENV_VARS:
        monkeypatch.delenv(key, raising=False)

    app = create_app()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200


def test_health_endpoint_returns_service_status() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "service_name": "assistant-api",
        "status": "ok",
    }


def test_settings_load_default_values(monkeypatch) -> None:
    for key in EXTERNAL_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("SERVICE_NAME", raising=False)

    settings = load_settings()

    assert settings.app_env == "local"
    assert settings.log_level == "INFO"
    assert settings.service_name == "assistant-api"
    assert settings.database_url == "postgresql+asyncpg://placeholder"
    assert settings.redis_url == "redis://placeholder"
    assert settings.sentry_dsn is None
    assert settings.feishu_webhook_verification_token == "placeholder-feishu-verification-token"
    assert settings.feishu_webhook_signing_secret == "placeholder-feishu-signing-secret"


def test_settings_support_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("SERVICE_NAME", "assistant-api-test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://example")
    monkeypatch.setenv("REDIS_URL", "redis://example")
    monkeypatch.setenv("SENTRY_DSN", "https://example.invalid/1")
    monkeypatch.setenv("FEISHU_WEBHOOK_VERIFICATION_TOKEN", "test-token")
    monkeypatch.setenv("FEISHU_WEBHOOK_SIGNING_SECRET", "test-signing-secret")

    settings = load_settings()

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.service_name == "assistant-api-test"
    assert settings.database_url == "postgresql+asyncpg://example"
    assert settings.redis_url == "redis://example"
    assert settings.sentry_dsn == "https://example.invalid/1"
    assert settings.feishu_webhook_verification_token == "test-token"
    assert settings.feishu_webhook_signing_secret == "test-signing-secret"


def test_structured_logging_does_not_emit_secrets(capsys) -> None:
    logger = configure_logging("INFO")

    logger.info("foundation_ready", api_key="secret-value")

    captured = capsys.readouterr()
    assert '"level":"info"' in captured.out
    assert '"message":"foundation_ready"' in captured.out
    assert "secret-value" not in captured.out


def test_unknown_route_returns_unified_json_error() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/missing")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Resource not found",
        }
    }


def test_defined_application_exception_returns_unified_json_error() -> None:
    app = create_app()

    @app.get("/raise-app-error")
    def raise_app_error() -> None:
        raise AppError(code="foundation_error", message="Foundation error", status_code=418)

    with TestClient(app) as client:
        response = client.get("/raise-app-error")

    assert response.status_code == 418
    assert response.json() == {
        "error": {
            "code": "foundation_error",
            "message": "Foundation error",
        }
    }
