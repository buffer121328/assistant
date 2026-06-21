from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    log_level: str = "INFO"
    service_name: str = "assistant-api"
    database_url: str = "postgresql+asyncpg://placeholder"
    redis_url: str = "redis://placeholder"
    sentry_dsn: str | None = None
    feishu_webhook_verification_token: str = "placeholder-feishu-verification-token"
    feishu_webhook_signing_secret: str = "placeholder-feishu-signing-secret"
    deepseek_api_key: str = "placeholder-deepseek-api-key"
    deepseek_base_url: str = "https://deepseek.invalid/v1"
    deepseek_light_model: str = "deepseek-light-placeholder"
    deepseek_standard_model: str = "deepseek-standard-placeholder"
    model_gateway_timeout_seconds: float = 10.0
    model_gateway_retry_attempts: int = 2

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def load_settings() -> Settings:
    return Settings()
