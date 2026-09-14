from infrastructure.settings.config import Settings


def test_model_timeout_default_is_30_seconds() -> None:
    assert Settings.model_fields["models_timeout_seconds"].default == 30.0
