from infrastructure.settings.config import Settings


def test_blank_search_provider_timeout_uses_default() -> None:
    settings = Settings.model_validate({"search_provider_timeout_seconds": ""})

    assert settings.search_provider_timeout_seconds is None
