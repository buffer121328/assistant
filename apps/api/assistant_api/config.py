from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    log_level: str = "INFO"
    service_name: str = "assistant-api"
    database_url: str = "postgresql+asyncpg://placeholder"
    redis_url: str = "redis://placeholder"
    sentry_dsn: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def load_settings() -> Settings:
    return Settings()
