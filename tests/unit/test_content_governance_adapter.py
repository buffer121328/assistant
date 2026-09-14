from __future__ import annotations

import pytest

from agent.governance.content import (
    ContentGuardProviderUnavailableError,
    LocalContentGuard,
    NoopContentGuard,
)
from infrastructure.adapters import content_governance
from infrastructure.adapters.content_governance import (
    GuardrailsAIContentGuard,
    build_content_guard,
)
from infrastructure.settings.config import Settings


def test_local_content_guard_is_the_default_and_does_not_load_optional_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def unexpected_import(name: str) -> object:
        calls.append(name)
        raise AssertionError("The local content guard must not load Guardrails AI")

    monkeypatch.setattr(
        content_governance.importlib,
        "import_module",
        unexpected_import,
    )

    guard = build_content_guard(
        Settings(
            database_url="sqlite+aiosqlite:///unused.db",
            content_governance_enabled=True,
            content_governance_provider="local",
        )
    )

    assert isinstance(guard, LocalContentGuard)
    assert calls == []


def test_content_governance_can_be_disabled_without_loading_optional_sdk() -> None:
    guard = build_content_guard(
        Settings(
            database_url="sqlite+aiosqlite:///unused.db",
            content_governance_enabled=False,
        )
    )

    assert isinstance(guard, NoopContentGuard)
    assert guard.enabled is False


def test_explicit_guardrails_provider_fails_with_bounded_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_name: str) -> object:
        raise ImportError("guardrails is not installed")

    monkeypatch.setattr(content_governance.importlib, "import_module", unavailable)

    with pytest.raises(ContentGuardProviderUnavailableError, match="unavailable"):
        build_content_guard(
            Settings(
                database_url="sqlite+aiosqlite:///unused.db",
                content_governance_provider="guardrails_ai",
            )
        )


def test_explicit_guardrails_provider_uses_only_the_internal_content_guard_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_modules: list[str] = []

    def fake_import(name: str) -> object:
        requested_modules.append(name)
        return object()

    monkeypatch.setattr(content_governance.importlib, "import_module", fake_import)

    guard = build_content_guard(
        Settings(
            database_url="sqlite+aiosqlite:///unused.db",
            content_governance_provider="guardrails_ai",
        ),
        sensitive_values=("secret-token",),
    )

    assert isinstance(guard, GuardrailsAIContentGuard)
    assert guard.provider == "guardrails_ai"
    assert requested_modules == ["guardrails"]
