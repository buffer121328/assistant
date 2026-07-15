from pathlib import Path

from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr

from assistant_api.config import Settings
from assistant_api.main import create_app
from assistant_desktop.client import DesktopApiClient
from packages.integrations import CredentialCipher, CredentialError

ROOT = Path(__file__).resolve().parents[2]


def test_local_api_authentication_protects_internal_routes() -> None:
    app = create_app(
        Settings(
            local_api_auth_required=True,
            local_api_token=SecretStr("test-local-token"),
        )
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        missing = client.get("/api/capabilities")
        wrong = client.get(
            "/api/capabilities", headers={"authorization": "Bearer wrong"}
        )
        allowed = client.get(
            "/api/capabilities",
            headers={"authorization": "Bearer test-local-token"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert allowed.status_code == 200
    assert "token" not in missing.text.lower()


def test_required_auth_without_token_fails_closed() -> None:
    app = create_app(
        Settings(local_api_auth_required=True, local_api_token=SecretStr(""))
    )
    with TestClient(app) as client:
        response = client.get("/api/capabilities")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "local_api_auth_unconfigured"


def test_compose_and_ci_define_engineering_boundaries() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert '"127.0.0.1:8000:8000"' in compose
    for name in (
        "LOCAL_API_AUTH_REQUIRED",
        "LOCAL_API_TOKEN",
        "ARTIFACTS_ROOT",
        "KNOWLEDGE_ROOT",
        "BROWSER_STATE_ROOT",
        "MEM0_CONFIG_PATH",
        "QUALITY_JUDGE_SAMPLE_RATE",
    ):
        assert name in compose
    for volume in ("artifacts-data", "knowledge-data", "browser-data"):
        assert volume in compose
    for command in ("pytest", "ruff check", "mypy", "alembic upgrade head"):
        assert command in workflow


def test_agent_engineering_packages_have_explicit_owners() -> None:
    for package in ("integrations", "knowledge", "notifications"):
        assert (ROOT / "packages" / package / "__init__.py").is_file()
    assert (ROOT / "tests/integration/README.md").is_file()


def test_desktop_client_sends_token_only_as_authorization_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": []})

    client = DesktopApiClient(
        base_url="http://127.0.0.1:8000",
        user_id="user-1",
        api_token="local-secret-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.list_tasks() == []
    finally:
        client.close()

    assert requests[0].headers["authorization"] == "Bearer local-secret-token"
    assert "local-secret-token" not in str(requests[0].url)


def test_credentials_are_versioned_encrypted_and_fail_closed() -> None:
    cipher = CredentialCipher("test-master-key-that-is-at-least-32-characters")
    encrypted = cipher.encrypt(
        {"username": "user@example.invalid", "password": "private-password"}
    )

    assert "private-password" not in encrypted
    assert cipher.decrypt(encrypted)["username"] == "user@example.invalid"
    try:
        CredentialCipher("")
    except CredentialError as exc:
        assert str(exc) == "credential_master_key_invalid"
    else:
        raise AssertionError("missing credential key must fail closed")

    try:
        cipher.decrypt(encrypted[:-2] + "xx")
    except CredentialError as exc:
        assert str(exc) == "credential_decryption_failed"
        assert "private-password" not in str(exc)
    else:
        raise AssertionError("tampered ciphertext must fail closed")
